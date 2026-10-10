"""Tabular baselines on the same temporal holdout the agent pipeline uses.

The question an interviewer will ask about an LLM fraud pipeline is whether it
beats a gradient boosting model that trains in two seconds. This answers it on
identical data, with the same split, and reports confidence intervals rather
than point estimates -- with 64 fraud rows in the holdout, a four-row swing
moves recall by six points.

Every feature here is computable at scoring time from the train split only:
account aggregates, device and IP sharing degrees, and the transaction's own
fields. `row_source` and the label are never used.

Usage:
    python evaluation/baseline_models.py
    python evaluation/baseline_models.py --train-frac 0.7 --bootstrap 10000
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

LABEL = "isFraud"
LEAKY_COLUMNS = {"row_source", "isFlaggedFraud"}


def build_features(train: pd.DataFrame, target: pd.DataFrame) -> pd.DataFrame:
    """Featurise `target` using state derived only from `train`."""
    account_stats = train.groupby("sender_account_no").agg(
        acct_txn_count=("amount", "size"),
        acct_avg_amount=("amount", "mean"),
        acct_max_amount=("amount", "max"),
        acct_drain_rate=("sender_balance_after", lambda s: float((s <= 0).mean())),
        acct_device_churn=("device_id", "nunique"),
        acct_ip_churn=("ip_address", "nunique"),
    )
    device_degree = train.groupby("device_id")["sender_account_no"].nunique()
    ip_degree = train.groupby("ip_address")["sender_account_no"].nunique()
    device_seen = train["device_id"].value_counts()
    ip_seen = train["ip_address"].value_counts()

    out = pd.DataFrame(index=target.index)
    out["amount"] = pd.to_numeric(target["amount"], errors="coerce")
    out["sender_balance_before"] = pd.to_numeric(target["sender_balance_before"], errors="coerce")
    out["sender_balance_after"] = pd.to_numeric(target["sender_balance_after"], errors="coerce")
    out["oldbalanceDest"] = pd.to_numeric(target["oldbalanceDest"], errors="coerce")
    out["newbalanceDest"] = pd.to_numeric(target["newbalanceDest"], errors="coerce")
    out["step"] = pd.to_numeric(target["step"], errors="coerce")
    out["drains_account"] = (out["sender_balance_after"] <= 0).astype(int)
    out["amount_over_balance"] = (
        out["amount"] / out["sender_balance_before"].replace(0, pd.NA)
    ).fillna(0).clip(0, 5)
    out["dest_was_empty"] = (out["oldbalanceDest"] <= 0).astype(int)

    for value in ("TRANSFER", "CASH_OUT", "PAYMENT", "CASH_IN", "DEBIT"):
        out[f"type_{value}"] = (target["type"] == value).astype(int)
    for value in ("SMS_OTP", "SMART_OTP", "FACE_ID"):
        out[f"auth_{value}"] = (target["auth_method"] == value).astype(int)

    joined = target["sender_account_no"].map(account_stats.to_dict("index"))
    for column in account_stats.columns:
        out[column] = [
            (row or {}).get(column, 0.0) if isinstance(row, dict) else 0.0 for row in joined
        ]
    out["acct_is_known"] = target["sender_account_no"].isin(account_stats.index).astype(int)

    out["device_accounts_sharing"] = target["device_id"].map(device_degree).fillna(0)
    out["ip_accounts_sharing"] = target["ip_address"].map(ip_degree).fillna(0)
    out["device_times_seen"] = target["device_id"].map(device_seen).fillna(0)
    out["ip_times_seen"] = target["ip_address"].map(ip_seen).fillna(0)
    out["device_is_new"] = (out["device_times_seen"] == 0).astype(int)
    out["ip_is_new"] = (out["ip_times_seen"] == 0).astype(int)

    return out.fillna(0.0)


def metrics(y_true: list[int], y_pred: list[int]) -> dict[str, float]:
    tp = sum(1 for p, t in zip(y_pred, y_true, strict=True) if p and t)
    fp = sum(1 for p, t in zip(y_pred, y_true, strict=True) if p and not t)
    fn = sum(1 for p, t in zip(y_pred, y_true, strict=True) if not p and t)
    tn = sum(1 for p, t in zip(y_pred, y_true, strict=True) if not p and not t)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": (tp + tn) / len(y_true),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
    }


def bootstrap_ci(
    y_true: list[int], y_pred: list[int], metric: str, rounds: int, seed: int
) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(y_true)
    values = []
    for _ in range(rounds):
        idx = [rng.randrange(n) for _ in range(n)]
        values.append(metrics([y_true[i] for i in idx], [y_pred[i] for i in idx])[metric])
    values.sort()
    return values[int(0.025 * rounds)], values[min(int(0.975 * rounds), rounds - 1)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Tabular baselines on the temporal holdout.")
    parser.add_argument("--csv", default="final.csv")
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument("--bootstrap", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="evaluation/results/baseline_models.json")
    args = parser.parse_args()

    df = pd.read_csv(args.csv).sort_values("step", kind="stable").reset_index(drop=True)
    cut = int(len(df) * args.train_frac)
    train, holdout = df.iloc[:cut], df.iloc[cut:]

    y_train = [int(v) for v in train[LABEL]]
    y_hold = [int(v) for v in holdout[LABEL]]
    print(
        f"train {len(train)} rows / {sum(y_train)} fraud   "
        f"holdout {len(holdout)} rows / {sum(y_hold)} fraud "
        f"({sum(y_hold) / len(y_hold):.1%})"
    )
    print(f"Features exclude {sorted(LEAKY_COLUMNS)} and the label.\n")

    x_train = build_features(train, train)
    x_hold = build_features(train, holdout)

    scaler = StandardScaler().fit(x_train)
    models = {
        "logistic_regression": (
            LogisticRegression(max_iter=2000, class_weight="balanced", random_state=args.seed),
            True,
        ),
        "gradient_boosting": (
            HistGradientBoostingClassifier(max_iter=300, random_state=args.seed),
            False,
        ),
    }

    results = {}
    for name, (model, needs_scaling) in models.items():
        xt = scaler.transform(x_train) if needs_scaling else x_train
        xh = scaler.transform(x_hold) if needs_scaling else x_hold
        model.fit(xt, y_train)
        y_pred = [int(v) for v in model.predict(xh)]
        m = metrics(y_hold, y_pred)
        cis = {
            metric: bootstrap_ci(y_hold, y_pred, metric, args.bootstrap, args.seed)
            for metric in ("precision", "recall", "f1")
        }
        results[name] = {**m, "ci95": {k: [round(v[0], 4), round(v[1], 4)] for k, v in cis.items()}}

        print(f"--- {name} ---")
        for metric in ("precision", "recall", "f1", "accuracy"):
            line = f"  {metric:<10} {m[metric]:.4f}"
            if metric in cis:
                line += f"   95% CI [{cis[metric][0]:.4f}, {cis[metric][1]:.4f}]"
            print(line)
        print(f"  TP={m['tp']} FP={m['fp']} FN={m['fn']} TN={m['tn']}\n")

    # Seen-before vs first-seen accounts. Catching a known mule says nothing
    # about what an investigation agent adds.
    known = set(train["sender_account_no"])
    seen_mask = [a in known for a in holdout["sender_account_no"]]
    best = max(results, key=lambda k: results[k]["f1"])
    model, needs_scaling = models[best]
    xh = scaler.transform(x_hold) if needs_scaling else x_hold
    y_pred = [int(v) for v in model.predict(xh)]

    print(f"--- {best}, split by whether the account was seen in train ---")
    strata = {}
    for label, want in (("seen before", True), ("first seen", False)):
        idx = [i for i, s in enumerate(seen_mask) if s == want]
        if not idx:
            continue
        m = metrics([y_hold[i] for i in idx], [y_pred[i] for i in idx])
        strata[label] = m
        print(
            f"  {label:<12} n={len(idx):<5} fraud={sum(y_hold[i] for i in idx):<4} "
            f"P={m['precision']:.4f} R={m['recall']:.4f} F1={m['f1']:.4f}"
        )

    payload = {
        "rows": len(df),
        "train_rows": len(train),
        "holdout_rows": len(holdout),
        "holdout_fraud": sum(y_hold),
        "bootstrap_rounds": args.bootstrap,
        "models": results,
        "stratified_best_model": {"model": best, "strata": strata},
        "features": list(x_train.columns),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nSaved -> {out}")


if __name__ == "__main__":
    main()
