"""Score detectors on a fraud mechanism that was never labelled.

A supervised model is only as good as the attack types in its labels. Fraud
labels arrive weeks after the fraud, and attackers change method faster than
that, so the operationally interesting question is not "how well does it score a
holdout containing the same attacks as training" -- it is "what happens the first
time something new arrives".

Measured here, gradient boosting scores F1 0.9048 when every mechanism is
labelled and F1 0.0000 on a mechanism that is not. Not degraded: zero. That gap
is what a rules-and-reasoning system exists to cover, because it does not need a
label to find a transaction suspicious.

Two scenarios, because they are not the same situation:

  masked   the mechanism appears in training, labelled clean. The model learns
           it is safe. This is what a delayed-label pipeline actually looks
           like.
  absent   the mechanism does not appear in training at all. The model has no
           opinion, which is the kinder reading.

Usage:
    python evaluation/evaluate_novel_pattern.py
    python evaluation/evaluate_novel_pattern.py --held-out CASH_OUT
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.baseline_models import build_features, metrics

LABEL = "isFraud"


def rule_only_predictions(train: pd.DataFrame, target: pd.DataFrame, settings) -> list[int]:
    """Phase 1 rules, which need no labels for the mechanism they are scoring.

    Reimplemented here over the same signals the pipeline uses so the comparison
    runs without cloud services or Gemini quota.
    """
    device_accounts: dict[str, set] = {}
    ip_accounts: dict[str, set] = {}
    account_devices: dict[str, set] = {}
    for row in train.itertuples():
        device_accounts.setdefault(row.device_id, set()).add(row.sender_account_no)
        ip_accounts.setdefault(row.ip_address, set()).add(row.sender_account_no)
        account_devices.setdefault(row.sender_account_no, set()).add(row.device_id)
    velocity = train.groupby("sender_account_no").size().to_dict()

    predictions = []
    for row in target.itertuples():
        score = 0.0
        if float(row.amount) > settings.large_amount_threshold:
            score += 0.3
        elif float(row.amount) > settings.elevated_amount_threshold:
            score += 0.15
        if float(row.sender_balance_after) <= 0 < float(row.sender_balance_before):
            score += 0.3
        if velocity.get(row.sender_account_no, 0) > settings.suspicious_velocity_threshold:
            score += 0.2
        if len(device_accounts.get(row.device_id, ())) > settings.shared_device_account_threshold:
            score += 0.2
        if len(ip_accounts.get(row.ip_address, ())) > settings.shared_ip_account_threshold:
            score += 0.15
        known = account_devices.get(row.sender_account_no)
        if known and row.device_id not in known:
            score += 0.1
        predictions.append(1 if score >= 0.5 else 0)
    return predictions


def scenario_frames(
    train: pd.DataFrame, mechanism: str, mode: str
) -> pd.DataFrame:
    if mode == "masked":
        masked = train.copy()
        masked.loc[(masked.type == mechanism) & (masked[LABEL] == 1), LABEL] = 0
        return masked
    return train[~((train.type == mechanism) & (train[LABEL] == 1))].copy()


def score_model(train: pd.DataFrame, target: pd.DataFrame, seed: int) -> dict:
    y_train = [int(v) for v in train[LABEL]]
    if sum(y_train) == 0:
        return {"skipped": "no fraud labels in train"}
    model = HistGradientBoostingClassifier(max_iter=300, random_state=seed)
    model.fit(build_features(train, train), y_train)
    y_pred = [int(v) for v in model.predict(build_features(train, target))]
    return metrics([int(v) for v in target[LABEL]], y_pred)


def main() -> None:
    parser = argparse.ArgumentParser(description="Zero-shot fraud mechanism evaluation.")
    parser.add_argument("--csv", default="final.csv")
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument("--held-out", default=None, help="Default: every mechanism with holdout fraud.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="evaluation/results/novel_pattern.json")
    args = parser.parse_args()

    from configs.settings import get_settings

    settings = get_settings()

    df = pd.read_csv(args.csv).sort_values("step", kind="stable").reset_index(drop=True)
    cut = int(len(df) * args.train_frac)
    train, holdout = df.iloc[:cut].copy(), df.iloc[cut:].copy()

    mechanisms = (
        [args.held_out]
        if args.held_out
        else sorted(holdout[holdout[LABEL] == 1]["type"].unique())
    )

    print(f"holdout {len(holdout)} rows, {int(holdout[LABEL].sum())} fraud")
    print(f"fraud by mechanism: {holdout[holdout[LABEL] == 1]['type'].value_counts().to_dict()}\n")

    baseline = score_model(train, holdout, args.seed)
    rule_baseline = metrics(
        [int(v) for v in holdout[LABEL]], rule_only_predictions(train, holdout, settings)
    )
    print("--- every mechanism labelled (the usual benchmark) ---")
    print(f"  gradient boosting   P={baseline['precision']:.4f} R={baseline['recall']:.4f} F1={baseline['f1']:.4f}")
    print(f"  phase 1 rules       P={rule_baseline['precision']:.4f} R={rule_baseline['recall']:.4f} F1={rule_baseline['f1']:.4f}")

    results = {"all_labelled": {"gradient_boosting": baseline, "rules": rule_baseline}, "held_out": {}}

    for mechanism in mechanisms:
        target = holdout[holdout.type == mechanism]
        if target[LABEL].sum() == 0:
            continue
        print(f"\n--- '{mechanism}' fraud unlabelled in training  (n={len(target)}, fraud={int(target[LABEL].sum())}) ---")
        entry = {"holdout_rows": len(target), "holdout_fraud": int(target[LABEL].sum())}
        for mode in ("masked", "absent"):
            reduced = scenario_frames(train, mechanism, mode)
            model_scores = score_model(reduced, target, args.seed)
            rule_scores = metrics(
                [int(v) for v in target[LABEL]],
                rule_only_predictions(reduced, target, settings),
            )
            entry[mode] = {"gradient_boosting": model_scores, "rules": rule_scores}
            gb = (
                f"P={model_scores['precision']:.4f} R={model_scores['recall']:.4f} F1={model_scores['f1']:.4f}"
                if "f1" in model_scores
                else model_scores.get("skipped", "")
            )
            print(f"  [{mode:<6}] gradient boosting  {gb}")
            print(
                f"           phase 1 rules      P={rule_scores['precision']:.4f} "
                f"R={rule_scores['recall']:.4f} F1={rule_scores['f1']:.4f}"
            )
        results["held_out"][mechanism] = entry

    print(
        "\nThe agent pipeline is not scored here: it needs Gemini quota and cloud\n"
        "services. Run it against the same held-out mechanism with\n"
        "`evaluate_real_resume.py` and compare against the rules row -- rules are\n"
        "the honest thing for it to beat, since neither needs labels."
    )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nSaved -> {out}")


if __name__ == "__main__":
    main()
