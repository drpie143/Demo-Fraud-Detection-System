from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _case_id(row_index: int, transaction_id: str) -> str:
    return f"{row_index}:{transaction_id}"


def _service_status() -> dict[str, bool]:
    from core.orchestration.pipeline import redis_service
    from infrastructure.databases.mongodb import mongodb_client
    from infrastructure.databases.neo4j import neo4j_client
    from infrastructure.databases.chroma import vector_store

    return {
        "redis_cloud": bool(getattr(redis_service, "is_connected", False)),
        "neo4j_cloud": bool(getattr(neo4j_client, "is_connected", False)),
        "mongodb_atlas": bool(getattr(mongodb_client, "is_connected", False)),
        "chromadb_cloud": bool(getattr(vector_store, "collection", None) is not None),
    }


def _require_cloud_services(status: dict[str, bool]) -> None:
    missing = [name for name, ok in status.items() if not ok]
    if missing:
        raise SystemExit(
            "Real benchmark aborted because these services are not connected: "
            + ", ".join(missing)
            + ". Fix .env or pass --allow-db-fallback if this run is intentionally mixed."
        )



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


def _metric_block(counts: dict[str, int], rows: int) -> dict[str, Any]:
    return {
        "rows": rows,
        "confusion_matrix": counts,
        **_score_counts(counts),
    }


def _configure_real_env() -> None:
    os.environ["AUTO_SEED_ON_STARTUP"] = "false"
    os.environ["DEMO_MODE"] = "false"
    os.environ["GEMINI_STRICT_ERRORS"] = "true"


def _transaction_from_series(row) -> "Transaction":
    from infrastructure.databases.seed_loader import transaction_from_row

    return transaction_from_row({key: row[key] for key in row.index})


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

def _build_output(
    *,
    args: argparse.Namespace,
    train_rows: int,
    holdout_rows: int,
    details: list[dict[str, Any]],
    service_status: dict[str, bool],
    cloud_push: dict[str, bool],
    stopped_reason: str | None,
) -> dict[str, Any]:
    completed = len(details)
    complete = completed == holdout_rows
    yellow_completed = sum(1 for item in details if item.get("phase1") == "yellow")

    payload: dict[str, Any] = {
        "updated_at": datetime.now().isoformat(),
        "benchmark": {
            "name": "real_services_resumable_temporal_holdout",
            "leakage_control": "profiles/risk/graph/history built only from train split",
            "train_frac": args.train_frac,
            "train_rows": train_rows,
            "holdout_rows": holdout_rows,
            "use_cloud_db": True,
            "use_gemini": True,
            "gemini_strict_errors": True,
            "cloud_push": cloud_push,
            "service_status": service_status,
            "estimated_gemini_calls_completed": yellow_completed * 6,
        },
        "progress": {
            "completed_rows": completed,
            "remaining_rows": max(holdout_rows - completed, 0),
            "complete": complete,
            "stopped_reason": stopped_reason,
        },
        "details": details,
    }

    if complete:

        rule_all = _empty_counts()
        pipeline_all = _empty_counts()
        yellow_rule_all = _empty_counts()
        yellow_pipeline = _empty_counts()
        phase1_yellow_rows = 0
        pipeline_yellow_rows = 0

        for item in details:
            actual_fraud = bool(item["actual_fraud"])
            phase1_level = item.get("phase1", "yellow")
            rule_predicted_fraud = phase1_level == "red"
            _update_counts(rule_all, rule_predicted_fraud, actual_fraud)
            if phase1_level == "yellow":
                phase1_yellow_rows += 1
                _update_counts(yellow_rule_all, rule_predicted_fraud, actual_fraud)

            pipeline_decision = item.get("pipeline_decision")
            pipeline_predicted_fraud = pipeline_decision == "block"
            _update_counts(pipeline_all, pipeline_predicted_fraud, actual_fraud)
            if phase1_level == "yellow":
                pipeline_yellow_rows += 1
                _update_counts(yellow_pipeline, pipeline_predicted_fraud, actual_fraud)

        payload.update({
            "rule_only_all_holdout": _metric_block(rule_all, completed),
            "pipeline_all_holdout": _metric_block(pipeline_all, completed),
            "rule_only_yellow_hard_cases": _metric_block(yellow_rule_all, phase1_yellow_rows),
            "pipeline_yellow_hard_cases": _metric_block(yellow_pipeline, pipeline_yellow_rows),
            "operational": {
                "phase1_review_rows": phase1_yellow_rows,
                "phase1_review_rate": round(phase1_yellow_rows / max(completed, 1), 4),
                "pipeline_coverage_rows": completed,
                "pipeline_coverage_rate": 1.0,
            },
        })

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resume-safe real DB + real Gemini benchmark for temporal holdout."
    )
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument("--limit", type=int, default=0, help="Limit holdout rows; 0 means all")
    parser.add_argument("--output", default="evaluation/results/real_llm_resume.json")
    parser.add_argument("--push-cloud-train", action="store_true", help="Destructively seed cloud DBs with train split. Use only on the first run or with --reset-progress.")
    parser.add_argument("--push-chroma", action="store_true", help="Also recreate Chroma knowledge collection when pushing train seed")
    parser.add_argument("--allow-db-fallback", action="store_true", help="Do not abort if one cloud DB falls back to simulator")
    parser.add_argument("--max-new-cases", type=int, default=0, help="Stop after this many newly completed rows; 0 means until quota/error/end")
    parser.add_argument("--max-new-yellow", type=int, default=0, help="Stop after this many newly completed yellow rows; 0 means unlimited")
    parser.add_argument("--reset-progress", action="store_true", help="Ignore existing checkpoint and start over")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if not 0 < args.train_frac < 1:
        raise SystemExit("--train-frac must be between 0 and 1")

    output = ROOT / args.output
    checkpoint = {} if args.reset_progress else _load_checkpoint(output)
    details: list[dict[str, Any]] = list(checkpoint.get("details", []))
    done = {item["case_id"] for item in details if item.get("case_id")}

    if details and args.push_cloud_train and not args.reset_progress:
        raise SystemExit(
            "Existing progress found. Do not use --push-cloud-train on resume, "
            "because it resets cloud DB state. Use --reset-progress to restart from zero."
        )
    _configure_real_env()

    from evaluation.benchmark_seed import build_seed_from_dataframe

    df = pd.read_csv(ROOT / "final.csv").sort_values(["step"]).reset_index(drop=True)
    df["_benchmark_row_index"] = range(len(df))
    cut = int(len(df) * args.train_frac)
    train_df = df.iloc[:cut].copy()
    test_df = df.iloc[cut:].copy()
    if args.limit and args.limit > 0:
        test_df = test_df.iloc[: args.limit].copy()

    if details:
        previous = checkpoint.get("benchmark", {})
        if (
            previous.get("train_frac") != args.train_frac
            or previous.get("train_rows") != len(train_df)
            or previous.get("holdout_rows") != len(test_df)
        ):
            raise SystemExit(
                "Existing checkpoint was created with a different split or limit. "
                "Use the same arguments or pass --reset-progress to restart."
            )

    seed_data = build_seed_from_dataframe(train_df.drop(columns=["_benchmark_row_index"]))
    cloud_push = checkpoint.get("benchmark", {}).get("cloud_push", {})
    if args.push_cloud_train:
        cloud_push = _push_cloud_train_seed(seed_data, push_chroma=args.push_chroma)

    if args.verbose:
        from core.orchestration.pipeline import FraudDetectionOrchestrator
    else:
        with contextlib.redirect_stdout(io.StringIO()):
            from core.orchestration.pipeline import FraudDetectionOrchestrator

    orchestrator = FraudDetectionOrchestrator()
    service_status: dict[str, bool] = {}
    stopped_reason: str | None = None
    new_cases = 0
    new_yellow = 0

    try:
        if args.verbose:
            orchestrator.initialize()
        else:
            with contextlib.redirect_stdout(io.StringIO()):
                orchestrator.initialize()

        service_status = _service_status()
        if not args.allow_db_fallback:
            _require_cloud_services(service_status)

        for _, row in test_df.iterrows():
            txn = _transaction_from_series(row.drop(labels=["_benchmark_row_index"]))
            cid = _case_id(int(row["_benchmark_row_index"]), txn.transaction_id)
            if cid in done:
                continue

            actual_fraud = bool(int(row["isFraud"]))
            try:
                if args.verbose:
                    result = orchestrator.process_transaction(txn)
                else:
                    with contextlib.redirect_stdout(io.StringIO()):
                        result = orchestrator.process_transaction(txn)
            except Exception as exc:
                stopped_reason = f"exception_before_save: {type(exc).__name__}: {exc}"
                break

            if result.get("error"):
                stopped_reason = f"pipeline_error_before_save: {result.get('error')}"
                break

            phase1_level = result.get("phase1_risk_level", "yellow")
            pipeline_decision = result.get("final_decision", "escalate")
            item = {
                "case_id": cid,
                "dataset_row_index": int(row["_benchmark_row_index"]),
                "transaction_id": txn.transaction_id,
                "sender_id": txn.sender_id,
                "receiver_id": txn.receiver_id,
                "amount": txn.amount,
                "actual_fraud": actual_fraud,
                "phase1": phase1_level,
                "rule_only_decision": "block" if phase1_level == "red" else "allow_or_review",
                "pipeline_executed": True,
                "pipeline_decision": pipeline_decision,
                "llm_required": phase1_level == "yellow",
                "completed_at": datetime.now().isoformat(),
            }
            details.append(item)
            done.add(cid)
            new_cases += 1
            if phase1_level == "yellow":
                new_yellow += 1

            payload = _build_output(
                args=args,
                train_rows=len(train_df),
                holdout_rows=len(test_df),
                details=details,
                service_status=service_status,
                cloud_push=cloud_push,
                stopped_reason=None,
            )
            _atomic_write_json(output, payload)

            if args.max_new_cases and new_cases >= args.max_new_cases:
                stopped_reason = f"max_new_cases reached: {args.max_new_cases}"
                break
            if args.max_new_yellow and new_yellow >= args.max_new_yellow:
                stopped_reason = f"max_new_yellow reached: {args.max_new_yellow}"
                break
    finally:
        if getattr(orchestrator, "_initialized", False):
            if args.verbose:
                orchestrator.shutdown()
            else:
                with contextlib.redirect_stdout(io.StringIO()):
                    orchestrator.shutdown()

    if stopped_reason is None and len(details) < len(test_df):
        stopped_reason = "stopped_before_completion"

    payload = _build_output(
        args=args,
        train_rows=len(train_df),
        holdout_rows=len(test_df),
        details=details,
        service_status=service_status,
        cloud_push=cloud_push,
        stopped_reason=stopped_reason,
    )
    _atomic_write_json(output, payload)

    printable = {k: v for k, v in payload.items() if k != "details"}
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    print(f"Saved checkpoint to {output}")


if __name__ == "__main__":
    main()