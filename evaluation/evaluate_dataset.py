from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _offline_env():
    os.environ.setdefault("DEMO_MODE", "true")
    os.environ.setdefault("REDIS_HOST", "localhost")
    for key in [
        "GEMINI_API_KEY",
        "GEMINI_API_KEY_PLANNER",
        "GEMINI_API_KEY_EXECUTOR",
        "GEMINI_API_KEY_EXECUTOR_POOL",
        "GEMINI_API_KEY_DETECTIVE",
        "GEMINI_API_KEY_VISION",
        "GEMINI_API_KEY_REPORT",
        "NEO4J_URI",
        "NEO4J_PASSWORD",
        "MONGODB_URI",
        "CHROMA_API_KEY",
        "REDIS_PASSWORD",
    ]:
        os.environ.setdefault(key, "")


def _score_counts(counts: dict[str, int]) -> dict[str, float]:
    precision = counts["tp"] / max(counts["tp"] + counts["fp"], 1)
    recall = counts["tp"] / max(counts["tp"] + counts["fn"], 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-9)
    accuracy = (counts["tp"] + counts["tn"]) / max(sum(counts.values()), 1)
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "accuracy": round(accuracy, 4),
    }


def _update_counts(counts: dict[str, int], predicted_fraud: bool, actual_fraud: bool) -> None:
    if predicted_fraud and actual_fraud:
        counts["tp"] += 1
    elif predicted_fraud and not actual_fraud:
        counts["fp"] += 1
    elif not predicted_fraud and actual_fraud:
        counts["fn"] += 1
    else:
        counts["tn"] += 1


def main():
    parser = argparse.ArgumentParser(description="Evaluate fraud detection decisions on final.csv")
    parser.add_argument("--limit", type=int, default=120, help="Number of rows to evaluate; use 0 for all rows")
    parser.add_argument("--output", default="evaluation/results/dataset_metrics.json")
    parser.add_argument("--verbose", action="store_true", help="Show full pipeline logs while evaluating")
    args = parser.parse_args()

    _offline_env()

    if args.verbose:
        from core.orchestration.pipeline import FraudDetectionOrchestrator
        from infrastructure.databases.seed_loader import load_final_csv_rows, transaction_from_row
    else:
        with contextlib.redirect_stdout(io.StringIO()):
            from core.orchestration.pipeline import FraudDetectionOrchestrator
            from infrastructure.databases.seed_loader import load_final_csv_rows, transaction_from_row

    rows = load_final_csv_rows()
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]

    orchestrator = FraudDetectionOrchestrator()
    final_counts = {"tp": 0, "tn": 0, "fp": 0, "fn": 0}
    rule_only_counts = {"tp": 0, "tn": 0, "fp": 0, "fn": 0}
    details = []

    try:
        if args.verbose:
            orchestrator.initialize()
        else:
            with contextlib.redirect_stdout(io.StringIO()):
                orchestrator.initialize()

        for row in rows:
            txn = transaction_from_row(row)
            if args.verbose:
                result = orchestrator.process_transaction(txn)
            else:
                with contextlib.redirect_stdout(io.StringIO()):
                    result = orchestrator.process_transaction(txn)

            predicted_fraud = result.get("final_decision") == "block"
            rule_only_fraud = result.get("phase1_risk_level") == "red"
            actual_fraud = row.get("isFraud") == "1"

            _update_counts(final_counts, predicted_fraud, actual_fraud)
            _update_counts(rule_only_counts, rule_only_fraud, actual_fraud)

            details.append({
                "transaction_id": txn.transaction_id,
                "sender_id": txn.sender_id,
                "receiver_id": txn.receiver_id,
                "amount": txn.amount,
                "actual_fraud": actual_fraud,
                "rule_only_decision": "block" if rule_only_fraud else "allow_or_review",
                "decision": result.get("final_decision"),
                "phase1": result.get("phase1_risk_level"),
            })
    finally:
        if args.verbose:
            orchestrator.shutdown()
        else:
            with contextlib.redirect_stdout(io.StringIO()):
                orchestrator.shutdown()

    metrics = {
        "evaluated_at": datetime.now().isoformat(),
        "rows": len(rows),
        "final_pipeline": {
            "confusion_matrix": final_counts,
            **_score_counts(final_counts),
        },
        "rule_only_baseline": {
            "confusion_matrix": rule_only_counts,
            **_score_counts(rule_only_counts),
        },
        "details": details,
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in metrics.items() if k != "details"}, ensure_ascii=False, indent=2))
    print(f"Saved details to {output}")


if __name__ == "__main__":
    main()
