"""Check whether any single feature gives the label away.

A synthetic fraud dataset is easy to build wrong. If you decide a transaction is
fraudulent and *then* give it weak authentication and a brand-new device, the
label becomes recoverable from one column, and every model trained on it looks
excellent while learning nothing.

This audit scores each feature on its own:

  AUC          how well the feature alone ranks fraud above non-fraud
  best rule    the single threshold or category test that maximises F1
  precision    of that rule -- 1.000 at meaningful recall means a giveaway

It fails when a feature crosses the configured gates, so a regenerated dataset
cannot quietly reintroduce leakage.

Usage:
    python evaluation/audit_leakage.py
    python evaluation/audit_leakage.py --csv final.csv --max-auc 0.75
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

LABEL = "isFraud"

# Identifier columns: high-cardinality keys that are not features on their own.
# They are audited through derived signals (reuse counts) instead of directly.
IDENTIFIER_COLUMNS = {
    "sender_account_no",
    "receiver_account_no",
    "device_id",
    "ip_address",
}

# Excluded from the audit because they are the label or a copy of it.
LABEL_COLUMNS = {LABEL, "isFlaggedFraud"}

# Provenance bookkeeping, not a feature. Every fraud row comes from PaySim, so
# `row_source` correlates with the label by construction. It is recorded for
# auditability and must never reach a model or a rule.
METADATA_COLUMNS = {"row_source"}

# Columns inherited from PaySim. Real fraud genuinely concentrates in TRANSFER
# and CASH_OUT and genuinely targets accounts with an empty balance, so a high
# AUC here is a property of fraud, not a defect we introduced. These are held to
# the giveaway gate only: they may be strongly predictive, they may not be
# deterministic.
PAYSIM_COLUMNS = {
    "step",
    "type",
    "amount",
    "sender_balance_before",
    "sender_balance_after",
    "oldbalanceDest",
    "newbalanceDest",
}

# Anything else is enrichment this project generated, and is held to both gates.
# `sender_account_no__txn_count` belongs here even though the account ids come
# from PaySim: how many rows each account contributes is a property of how the
# sample was drawn, which is ours.


def auc_score(values: list[float], labels: list[int]) -> float:
    """Rank-based AUC. 0.5 is uninformative, 1.0 separates perfectly."""
    pairs = sorted(zip(values, labels, strict=True))
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5

    # Average ranks so ties do not inflate the score.
    ranks: list[float] = [0.0] * len(pairs)
    i = 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1

    rank_sum_pos = sum(r for r, (_, lab) in zip(ranks, pairs, strict=True) if lab == 1)
    return (rank_sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def rule_metrics(mask: list[bool], labels: list[int]) -> dict[str, float]:
    tp = sum(1 for m, y in zip(mask, labels, strict=True) if m and y)
    fp = sum(1 for m, y in zip(mask, labels, strict=True) if m and not y)
    fn = sum(1 for m, y in zip(mask, labels, strict=True) if not m and y)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp}


def best_categorical_rule(series: pd.Series, labels: list[int]) -> tuple[str, dict]:
    """Best single-category membership test, by F1."""
    best_desc, best = "", {"f1": -1.0}
    for value in series.dropna().unique():
        mask = [v == value for v in series]
        metrics = rule_metrics(mask, labels)
        if metrics["f1"] > best["f1"]:
            best_desc, best = f"== {value!r}", metrics
    return best_desc, best


def best_numeric_rule(series: pd.Series, labels: list[int]) -> tuple[str, dict]:
    """Best single threshold test, by F1, scanning quantiles."""
    values = pd.to_numeric(series, errors="coerce")
    candidates = sorted({float(v) for v in values.quantile([i / 20 for i in range(1, 20)])})
    best_desc, best = "", {"f1": -1.0}
    for threshold in candidates:
        for desc, mask in (
            (f"> {threshold:.4g}", [bool(v > threshold) for v in values]),
            (f"<= {threshold:.4g}", [bool(v <= threshold) for v in values]),
        ):
            metrics = rule_metrics(mask, labels)
            if metrics["f1"] > best["f1"]:
                best_desc, best = desc, metrics
    return best_desc, best


def derived_identifier_features(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Turn identifier columns into the signals a detector could actually use."""
    derived: dict[str, pd.Series] = {}
    for column in ("device_id", "ip_address"):
        if column not in df:
            continue
        counts = Counter(df[column])
        derived[f"{column}__times_seen"] = df[column].map(counts)
        accounts_per_value: dict[str, set] = defaultdict(set)
        for value, account in zip(df[column], df["sender_account_no"], strict=True):
            accounts_per_value[value].add(account)
        derived[f"{column}__accounts_sharing"] = df[column].map(
            {k: len(v) for k, v in accounts_per_value.items()}
        )
    if "sender_account_no" in df:
        counts = Counter(df["sender_account_no"])
        derived["sender_account_no__txn_count"] = df["sender_account_no"].map(counts)
    return derived


def audit(df: pd.DataFrame, max_auc: float, max_precision_at_recall: float, min_recall: float) -> dict:
    labels = [int(v) for v in df[LABEL]]
    base_rate = sum(labels) / len(labels)

    columns: dict[str, pd.Series] = {
        name: df[name]
        for name in df.columns
        if name not in LABEL_COLUMNS
        and name not in IDENTIFIER_COLUMNS
        and name not in METADATA_COLUMNS
    }
    columns.update(derived_identifier_features(df))

    findings = []
    for name, series in sorted(columns.items()):
        numeric = pd.to_numeric(series, errors="coerce")
        is_numeric = numeric.notna().all()

        if is_numeric:
            auc = auc_score([float(v) for v in numeric], labels)
            desc, metrics = best_numeric_rule(series, labels)
        else:
            # Category AUC uses each category's fraud rate as the score.
            rates = df.groupby(series)[LABEL].mean()
            auc = auc_score([float(rates[v]) for v in series], labels)
            desc, metrics = best_categorical_rule(series, labels)

        auc = max(auc, 1.0 - auc)  # direction-agnostic
        giveaway = (
            metrics["precision"] >= max_precision_at_recall and metrics["recall"] >= min_recall
        )
        # PaySim columns are judged on the giveaway gate only.
        source = "paysim" if name in PAYSIM_COLUMNS else "generated"
        auc_exceeded = auc > max_auc and source == "generated"
        findings.append(
            {
                "feature": name,
                "source": source,
                "auc": round(auc, 4),
                "best_rule": desc,
                "precision": round(metrics["precision"], 4),
                "recall": round(metrics["recall"], 4),
                "f1": round(metrics["f1"], 4),
                "tp": metrics["tp"],
                "fp": metrics["fp"],
                "auc_exceeded": auc_exceeded,
                "giveaway": giveaway,
            }
        )

    findings.sort(key=lambda item: -item["auc"])
    return {
        "rows": len(df),
        "fraud": sum(labels),
        "base_rate": round(base_rate, 4),
        "gates": {
            "max_auc": max_auc,
            "max_precision_at_recall": max_precision_at_recall,
            "min_recall": min_recall,
        },
        "features": findings,
        "failures": [f for f in findings if f["auc_exceeded"] or f["giveaway"]],
    }


def provenance_detectability(df: pd.DataFrame) -> dict | None:
    """Can a model tell generated rows from PaySim rows?

    If it can, and generated rows carry a different class balance, then any
    classifier can separate the labels by detecting the generator instead of
    detecting fraud. The first attempt at this dataset failed exactly here:
    every generated row satisfied `newbalanceDest - oldbalanceDest == amount`
    and none had a zero destination balance, while real rows do so 55% and 49%
    of the time -- and every generated row was legitimate.

    A single-feature audit cannot see this, because the giveaway is a
    combination.
    """
    if "row_source" not in df or df["row_source"].nunique() < 2:
        return None

    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.model_selection import cross_val_predict
    except ImportError:
        return {"skipped": "scikit-learn is not installed"}

    y = (df["row_source"] != "paysim").astype(int)
    numeric = df[[c for c in PAYSIM_COLUMNS if c in df]].apply(pd.to_numeric, errors="coerce")
    numeric = numeric.join(pd.get_dummies(df["type"], prefix="type"))
    numeric["dest_arithmetic_exact"] = (
        (numeric["newbalanceDest"] - numeric["oldbalanceDest"] - numeric["amount"]).abs() < 0.005
    ).astype(int)
    numeric["dest_empty"] = (numeric["oldbalanceDest"] <= 0).astype(int)
    numeric = numeric.fillna(0.0)

    model = HistGradientBoostingClassifier(max_iter=200, random_state=0)
    proba = cross_val_predict(model, numeric, y, cv=5, method="predict_proba")[:, 1]
    auc = auc_score(proba.tolist(), y.tolist())

    fraud_by_source = df.groupby("row_source")[LABEL].mean().to_dict()
    return {
        "auc": round(max(auc, 1.0 - auc), 4),
        "fraud_rate_by_source": {k: round(float(v), 4) for k, v in fraud_by_source.items()},
        "rows_by_source": df["row_source"].value_counts().to_dict(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit a fraud dataset for single-feature leakage.")
    parser.add_argument("--csv", default="final.csv")
    parser.add_argument("--max-auc", type=float, default=0.75)
    parser.add_argument(
        "--max-precision",
        type=float,
        default=0.98,
        help="A rule at or above this precision, with at least --min-recall, counts as a giveaway.",
    )
    parser.add_argument("--min-recall", type=float, default=0.10)
    parser.add_argument(
        "--max-provenance-auc",
        type=float,
        default=0.70,
        help="How well a model may distinguish generated rows from PaySim rows.",
    )
    parser.add_argument("--output", default=None)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero when any feature fails a gate. Use this in CI.",
    )
    args = parser.parse_args()

    df = pd.read_csv(args.csv)
    report = audit(df, args.max_auc, args.max_precision, args.min_recall)

    print(f"Rows {report['rows']}  fraud {report['fraud']}  base rate {report['base_rate']:.1%}")
    print(
        f"Gates: no rule with precision >= {args.max_precision} at recall >= {args.min_recall} "
        f"(all columns), and AUC <= {args.max_auc} (generated columns only)\n"
    )
    print(f"{'feature':<34}{'source':<11}{'AUC':>7}{'P':>8}{'R':>8}{'F1':>8}  best single rule")
    print("-" * 104)
    for f in report["features"]:
        flag = "  <-- FAIL" if (f["auc_exceeded"] or f["giveaway"]) else ""
        print(
            f"{f['feature']:<34}{f['source']:<11}{f['auc']:>7.4f}{f['precision']:>8.4f}"
            f"{f['recall']:>8.4f}{f['f1']:>8.4f}  {f['best_rule']}{flag}"
        )

    if report["failures"]:
        print(f"\n{len(report['failures'])} feature(s) failed:")
        for f in report["failures"]:
            reason = []
            if f["auc_exceeded"]:
                reason.append(f"AUC {f['auc']:.4f} > {args.max_auc}")
            if f["giveaway"]:
                reason.append(
                    f"rule `{f['feature']} {f['best_rule']}` reaches precision "
                    f"{f['precision']:.4f} at recall {f['recall']:.4f}"
                )
            print(f"  {f['feature']}: {'; '.join(reason)}")
        print("\nA feature that identifies the label on its own makes every model")
        print("trained on this data meaningless -- a one-line rule would win.")
    else:
        print("\nNo single feature crosses the gates.")

    provenance = provenance_detectability(df)
    provenance_failed = False
    if provenance and "auc" in provenance:
        report["provenance_detectability"] = provenance
        rates = provenance["fraud_rate_by_source"]
        spread = max(rates.values()) - min(rates.values()) if len(rates) > 1 else 0.0
        provenance_failed = provenance["auc"] > args.max_provenance_auc and spread > 0.05
        print("\n--- Can a model tell generated rows from PaySim rows? ---")
        print(f"  detector AUC: {provenance['auc']:.4f}  (gate {args.max_provenance_auc})")
        for source, rate in rates.items():
            print(f"  fraud rate in {source:<20} {rate:.4f}  (n={provenance['rows_by_source'][source]})")
        if provenance_failed:
            print(
                "  FAIL: generated rows are both detectable and carry a different\n"
                "  class balance, so a model can separate labels by spotting the generator."
            )
        else:
            print("  OK")
        report["provenance_failed"] = provenance_failed

    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nSaved -> {args.output}")

    if args.check and (report["failures"] or provenance_failed):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
