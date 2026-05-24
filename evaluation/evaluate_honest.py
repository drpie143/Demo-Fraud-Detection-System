from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


GEMINI_KEYS = [
    "GEMINI_API_KEY",
    "GEMINI_API_KEY_PLANNER",
    "GEMINI_API_KEY_EXECUTOR",
    "GEMINI_API_KEY_EXECUTOR_POOL",
    "GEMINI_API_KEY_DETECTIVE",
    "GEMINI_API_KEY_VISION",
    "GEMINI_API_KEY_REPORT",
]


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


def _empty_counts() -> dict[str, int]:
    return {"tp": 0, "tn": 0, "fp": 0, "fn": 0}


def _update_counts(counts: dict[str, int], predicted_fraud: bool, actual_fraud: bool) -> None:
    if predicted_fraud and actual_fraud:
        counts["tp"] += 1
    elif predicted_fraud and not actual_fraud:
        counts["fp"] += 1
    elif not predicted_fraud and actual_fraud:
        counts["fn"] += 1
    else:
        counts["tn"] += 1


def _metric_block(counts: dict[str, int], rows: int) -> dict:
    return {
        "rows": rows,
        "confusion_matrix": counts,
        **_score_counts(counts),
    }


def _configure_env(*, use_cloud_db: bool, use_gemini: bool) -> None:
    os.environ["AUTO_SEED_ON_STARTUP"] = "false"
    os.environ["DEMO_MODE"] = "false" if use_cloud_db else "true"

    if not use_gemini:
        for key in GEMINI_KEYS:
            os.environ[key] = ""

    if not use_cloud_db:
        os.environ.setdefault("REDIS_HOST", "localhost")
        os.environ.setdefault("NEO4J_PASSWORD", "")
        os.environ.setdefault("MONGODB_URI", "")
        os.environ.setdefault("CHROMA_API_KEY", "")
        os.environ.setdefault("REDIS_PASSWORD", "")


def _transaction_from_series(row) -> "Transaction":
    from infrastructure.databases.seed_loader import transaction_from_row

    return transaction_from_row({key: row[key] for key in row.index})


def _run_phase1_only(transaction) -> dict:
    from core.orchestration.pipeline import make_initial_state, phase1_screening

    return phase1_screening(make_initial_state(transaction))


def _push_cloud_train_seed(seed_data: dict[str, list[dict]], *, push_chroma: bool) -> dict[str, bool]:
    from evaluation.push_seed_data import push_chromadb, push_mongodb, push_neo4j, push_redis

    profiles = seed_data["profiles"]
    transactions = seed_data["transactions"]
    raw_edges = seed_data["raw_edges"]
    return {
        "redis": push_redis(profiles, raw_edges),
        "neo4j": push_neo4j(profiles, raw_edges),
        "mongodb": push_mongodb(profiles, transactions),
        "chromadb": push_chromadb() if push_chroma else False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Leakage-controlled benchmark: train-only seed, holdout evaluation."
    )
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument("--limit", type=int, default=0, help="Limit holdout rows; 0 means all")
    parser.add_argument("--output", default="evaluation/results/honest_metrics.json")
    parser.add_argument("--use-cloud-db", action="store_true", help="Use configured cloud DBs instead of simulators")
    parser.add_argument("--push-cloud-train", action="store_true", help="Destructively seed cloud DBs with train split")
    parser.add_argument("--push-chroma", action="store_true", help="Also recreate Chroma knowledge collection")
    parser.add_argument("--use-gemini", action="store_true", help="Use Gemini for Phase 2/3 where pipeline runs")
    parser.add_argument(
        "--max-yellow-pipeline",
        type=int,
        default=None,
        help="Max Phase-1 yellow rows to send through full pipeline; -1 means all",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if not 0 < args.train_frac < 1:
        raise SystemExit("--train-frac must be between 0 and 1")

    if args.max_yellow_pipeline is None:
        max_yellow_pipeline = 20 if args.use_gemini else -1
    else:
        max_yellow_pipeline = args.max_yellow_pipeline

    _configure_env(use_cloud_db=args.use_cloud_db, use_gemini=args.use_gemini)

    from evaluation.benchmark_seed import apply_seed_to_simulators, build_seed_from_dataframe

    df = pd.read_csv(ROOT / "final.csv").sort_values(["step"]).reset_index(drop=True)
    cut = int(len(df) * args.train_frac)
    train_df = df.iloc[:cut].copy()
    test_df = df.iloc[cut:].copy()
    if args.limit and args.limit > 0:
        test_df = test_df.iloc[: args.limit].copy()

    seed_data = build_seed_from_dataframe(train_df)
    cloud_push = {}
    if args.use_cloud_db and args.push_cloud_train:
        cloud_push = _push_cloud_train_seed(seed_data, push_chroma=args.push_chroma)

    if args.verbose:
        from core.orchestration.pipeline import FraudDetectionOrchestrator, redis_service
    else:
        with contextlib.redirect_stdout(io.StringIO()):
            from core.orchestration.pipeline import FraudDetectionOrchestrator, redis_service

    orchestrator = FraudDetectionOrchestrator()

    rule_all = _empty_counts()
    pipeline_all = _empty_counts()
    yellow_rule_all = _empty_counts()
    yellow_pipeline = _empty_counts()
    details = []
    phase1_rows = 0
    pipeline_rows = 0
    phase1_yellow_rows = 0
    pipeline_yellow_rows = 0
    yellow_budget_used = 0
    review_rows = 0

    try:
        if args.verbose:
            orchestrator.initialize()
        else:
            with contextlib.redirect_stdout(io.StringIO()):
                orchestrator.initialize()

        if not args.use_cloud_db:
            apply_seed_to_simulators(seed_data, redis_service=redis_service)

        for _, row in test_df.iterrows():
            txn = _transaction_from_series(row)
            actual_fraud = bool(int(row["isFraud"]))

            should_run_pipeline = (
                max_yellow_pipeline == -1
                or yellow_budget_used < max_yellow_pipeline
            )

            if should_run_pipeline:
                if args.verbose:
                    result = orchestrator.process_transaction(txn)
                else:
                    with contextlib.redirect_stdout(io.StringIO()):
                        result = orchestrator.process_transaction(txn)
                pipeline_executed = True
                phase1_level = result.get("phase1_risk_level", "yellow")
                if phase1_level == "yellow":
                    yellow_budget_used += 1
            else:
                if args.verbose:
                    result = _run_phase1_only(txn)
                else:
                    with contextlib.redirect_stdout(io.StringIO()):
                        result = _run_phase1_only(txn)
                pipeline_executed = False
                phase1_level = result.get("phase1_risk_level", "yellow")

            phase1_rows += 1
            rule_predicted_fraud = phase1_level == "red"
            if phase1_level == "yellow":
                review_rows += 1
                phase1_yellow_rows += 1
                _update_counts(yellow_rule_all, rule_predicted_fraud, actual_fraud)
            _update_counts(rule_all, rule_predicted_fraud, actual_fraud)

            pipeline_decision = None
            if pipeline_executed:
                pipeline_rows += 1
                pipeline_decision = result.get("final_decision", "escalate")
                pipeline_predicted_fraud = pipeline_decision == "block"
                _update_counts(pipeline_all, pipeline_predicted_fraud, actual_fraud)
                if phase1_level == "yellow":
                    pipeline_yellow_rows += 1
                    _update_counts(yellow_pipeline, pipeline_predicted_fraud, actual_fraud)

            details.append({
                "transaction_id": txn.transaction_id,
                "sender_id": txn.sender_id,
                "receiver_id": txn.receiver_id,
                "amount": txn.amount,
                "actual_fraud": actual_fraud,
                "phase1": phase1_level,
                "rule_only_decision": "block" if rule_predicted_fraud else "allow_or_review",
                "pipeline_executed": pipeline_executed,
                "pipeline_decision": pipeline_decision,
            })
    finally:
        if args.verbose:
            orchestrator.shutdown()
        else:
            with contextlib.redirect_stdout(io.StringIO()):
                orchestrator.shutdown()

    metrics = {
        "evaluated_at": datetime.now().isoformat(),
        "benchmark": {
            "name": "temporal_holdout_train_only_seed",
            "leakage_control": "profiles/risk/graph/history built only from train split",
            "train_frac": args.train_frac,
            "train_rows": len(train_df),
            "holdout_rows": len(test_df),
            "use_cloud_db": args.use_cloud_db,
            "use_gemini": args.use_gemini,
            "max_yellow_pipeline": max_yellow_pipeline,
            "yellow_budget_used": yellow_budget_used,
            "estimated_gemini_calls": yellow_budget_used * 6 if args.use_gemini else 0,
            "cloud_push": cloud_push,
        },
        "rule_only_all_holdout": _metric_block(rule_all, phase1_rows),
        "pipeline_executed_subset": _metric_block(pipeline_all, pipeline_rows),
        "rule_only_yellow_hard_cases": _metric_block(yellow_rule_all, phase1_yellow_rows),
        "pipeline_yellow_hard_cases": _metric_block(yellow_pipeline, pipeline_yellow_rows),
        "operational": {
            "phase1_review_rows": review_rows,
            "phase1_review_rate": round(review_rows / max(phase1_rows, 1), 4),
            "pipeline_coverage_rows": pipeline_rows,
            "pipeline_coverage_rate": round(pipeline_rows / max(phase1_rows, 1), 4),
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
