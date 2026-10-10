"""Find where an investigation agent can beat a gradient boosting model.

"The LLM lost to XGBoost" is only an argument against the agent if the agent is
asked to do the same job. A model that scores every transaction from tabular
history is very good at transactions that look like its history, and much weaker
elsewhere. This measures where "elsewhere" is, and how much is available there:

  1. Is the model's advantage real, or is it leaning on one artefact?
  2. How much does it lose on accounts it has never seen?
  3. How wide is its uncertain band, and what would perfect adjudication of that
     band be worth?

The third number is the agent's actual budget. If an oracle resolving the
uncertain cases only buys two points of F1, no amount of reasoning will earn its
latency. If it buys ten, there is something to compete for.

Usage:
    python evaluation/analyze_agent_headroom.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.baseline_models import build_features, metrics

LABEL = "isFraud"


def band_metrics(y_true: list[int], proba: list[float], low: float, high: float) -> dict:
    """Metrics if everything inside [low, high] were adjudicated perfectly."""
    inside = [i for i, p in enumerate(proba) if low <= p <= high]
    y_pred = [1 if p >= 0.5 else 0 for p in proba]
    oracle = list(y_pred)
    for i in inside:
        oracle[i] = y_true[i]
    return {
        "band": [low, high],
        "n_in_band": len(inside),
        "share_in_band": round(len(inside) / len(proba), 4),
        "fraud_in_band": sum(y_true[i] for i in inside),
        "model_f1": round(metrics(y_true, y_pred)["f1"], 4),
        "oracle_f1": round(metrics(y_true, oracle)["f1"], 4),
        "headroom": round(metrics(y_true, oracle)["f1"] - metrics(y_true, y_pred)["f1"], 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure the agent's opportunity.")
    parser.add_argument("--csv", default="final.csv")
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="evaluation/results/agent_headroom.json")
    args = parser.parse_args()

    df = pd.read_csv(args.csv).sort_values("step", kind="stable").reset_index(drop=True)
    cut = int(len(df) * args.train_frac)
    train, holdout = df.iloc[:cut], df.iloc[cut:]
    y_train = [int(v) for v in train[LABEL]]
    y_hold = [int(v) for v in holdout[LABEL]]

    x_train = build_features(train, train)
    x_hold = build_features(train, holdout)

    model = HistGradientBoostingClassifier(max_iter=300, random_state=args.seed)
    model.fit(x_train, y_train)
    proba = [float(p) for p in model.predict_proba(x_hold)[:, 1]]
    y_pred = [1 if p >= 0.5 else 0 for p in proba]
    overall = metrics(y_hold, y_pred)

    print(f"Holdout {len(holdout)} rows, {sum(y_hold)} fraud")
    print(f"Gradient boosting F1 {overall['f1']:.4f}\n")

    # 1. What is the model leaning on?
    importance = permutation_importance(
        model, x_hold, y_hold, n_repeats=10, random_state=args.seed, scoring="f1"
    )
    ranked = sorted(
        zip(x_hold.columns, importance.importances_mean, strict=True),
        key=lambda item: -item[1],
    )
    print("--- what the model relies on (permutation importance, F1) ---")
    for name, value in ranked[:8]:
        print(f"  {name:<28}{value:+.4f}")
    top_share = ranked[0][1] / sum(max(v, 0) for _, v in ranked) if ranked else 0
    print(f"  top feature accounts for {top_share:.1%} of total importance\n")

    # 2. Where does it get weaker?
    known = set(train["sender_account_no"])
    seen = [a in known for a in holdout["sender_account_no"]]
    print("--- by account familiarity ---")
    strata = {}
    for label, want in (("seen before", True), ("first seen", False)):
        idx = [i for i, s in enumerate(seen) if s == want]
        if not idx:
            continue
        m = metrics([y_hold[i] for i in idx], [y_pred[i] for i in idx])
        strata[label] = {k: round(v, 4) if isinstance(v, float) else v for k, v in m.items()}
        print(
            f"  {label:<12} n={len(idx):<5} fraud={sum(y_hold[i] for i in idx):<4} "
            f"P={m['precision']:.4f} R={m['recall']:.4f} F1={m['f1']:.4f}"
        )
    gap = strata.get("seen before", {}).get("f1", 0) - strata.get("first seen", {}).get("f1", 0)
    print(f"  gap: {gap:+.4f} F1 on accounts the model has no history for\n")

    # 3. How much is perfect adjudication of the uncertain band worth?
    print("--- if an agent resolved the model's uncertain cases perfectly ---")
    print(f"{'band':<18}{'rows':>6}{'share':>8}{'fraud':>7}{'model F1':>10}{'oracle F1':>11}{'gain':>8}")
    bands = []
    for low, high in ((0.4, 0.6), (0.3, 0.7), (0.2, 0.8), (0.1, 0.9)):
        b = band_metrics(y_hold, proba, low, high)
        bands.append(b)
        print(
            f"  [{low:.1f}, {high:.1f}]      {b['n_in_band']:>6}{b['share_in_band']:>8.1%}"
            f"{b['fraud_in_band']:>7}{b['model_f1']:>10.4f}{b['oracle_f1']:>11.4f}{b['headroom']:>8.4f}"
        )

    print("\n--- the same, restricted to first-seen accounts ---")
    idx = [i for i, s in enumerate(seen) if not s]
    sub_true = [y_hold[i] for i in idx]
    sub_proba = [proba[i] for i in idx]
    first_seen_bands = []
    for low, high in ((0.3, 0.7), (0.2, 0.8)):
        b = band_metrics(sub_true, sub_proba, low, high)
        first_seen_bands.append(b)
        print(
            f"  [{low:.1f}, {high:.1f}]      {b['n_in_band']:>6}{b['share_in_band']:>8.1%}"
            f"{b['fraud_in_band']:>7}{b['model_f1']:>10.4f}{b['oracle_f1']:>11.4f}{b['headroom']:>8.4f}"
        )

    payload = {
        "holdout_rows": len(holdout),
        "holdout_fraud": sum(y_hold),
        "model_f1": round(overall["f1"], 4),
        "permutation_importance": [[n, round(float(v), 5)] for n, v in ranked],
        "by_account_familiarity": strata,
        "uncertain_band_headroom": bands,
        "first_seen_band_headroom": first_seen_bands,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nSaved -> {out}")


if __name__ == "__main__":
    main()
