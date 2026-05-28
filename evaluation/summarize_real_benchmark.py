from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "evaluation" / "results" / "real_llm_resume.json"


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


def _counts(rows: list[dict[str, Any]], predict_fraud: Callable[[dict[str, Any]], bool]) -> dict[str, int]:
    counts = {"tp": 0, "tn": 0, "fp": 0, "fn": 0}
    for item in rows:
        predicted_fraud = predict_fraud(item)
        actual_fraud = bool(item.get("actual_fraud"))
        if predicted_fraud and actual_fraud:
            counts["tp"] += 1
        elif predicted_fraud and not actual_fraud:
            counts["fp"] += 1
        elif not predicted_fraud and actual_fraud:
            counts["fn"] += 1
        else:
            counts["tn"] += 1
    return counts


def _metric_block(rows: list[dict[str, Any]], predict_fraud: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    counts = _counts(rows, predict_fraud)
    return {
        "rows": len(rows),
        "confusion_matrix": counts,
        **_score_counts(counts),
    }


def summarize(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    details = data.get("details", [])
    yellow = [item for item in details if item.get("phase1") == "yellow"]
    case_ids = [item.get("case_id") for item in details]

    return {
        "source": str(path),
        "progress": data.get("progress", {}),
        "benchmark": data.get("benchmark", {}),
        "validation": {
            "details_rows": len(details),
            "duplicate_case_ids": len(case_ids) - len(set(case_ids)),
            "missing_case_ids": sum(1 for case_id in case_ids if not case_id),
            "has_final_metrics": all(
                key in data
                for key in [
                    "rule_only_all_holdout",
                    "pipeline_all_holdout",
                    "rule_only_yellow_hard_cases",
                    "pipeline_yellow_hard_cases",
                ]
            ),
        },
        "distribution": {
            "phase1": dict(Counter(item.get("phase1") for item in details)),
            "pipeline_decision": dict(Counter(item.get("pipeline_decision") for item in details)),
            "actual_fraud": dict(Counter(str(bool(item.get("actual_fraud"))) for item in details)),
        },
        "rule_only_all_holdout": _metric_block(details, lambda item: item.get("phase1") == "red"),
        "pipeline_all_holdout": _metric_block(details, lambda item: item.get("pipeline_decision") == "block"),
        "rule_only_yellow_hard_cases": _metric_block(yellow, lambda item: False),
        "pipeline_yellow_hard_cases": _metric_block(yellow, lambda item: item.get("pipeline_decision") == "block"),
    }


def _table_row(name: str, block: dict[str, Any]) -> str:
    cm = block["confusion_matrix"]
    return (
        f"| {name} | {block['rows']} | {cm['tp']} | {cm['tn']} | {cm['fp']} | {cm['fn']} | "
        f"{block['precision']:.4f} | {block['recall']:.4f} | {block['f1']:.4f} | {block['accuracy']:.4f} |"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize the real DB + Gemini benchmark checkpoint.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of markdown tables")
    args = parser.parse_args()

    summary = summarize(Path(args.input))
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    progress = summary["progress"]
    benchmark = summary["benchmark"]
    print("Real DB + Gemini Benchmark")
    print(f"Completed: {progress.get('completed_rows')}/{benchmark.get('holdout_rows')} rows")
    print(f"Complete: {progress.get('complete')}")
    print(f"Services: {benchmark.get('service_status')}")
    print()
    print("| Model | Rows | TP | TN | FP | FN | Precision | Recall | F1 | Accuracy |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    print(_table_row("Real Gemini pipeline", summary["pipeline_all_holdout"]))
    print(_table_row("Rule-only baseline", summary["rule_only_all_holdout"]))
    print()
    print("Yellow hard cases")
    print()
    print("| Model | Rows | TP | TN | FP | FN | Precision | Recall | F1 | Accuracy |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    print(_table_row("Real Gemini pipeline", summary["pipeline_yellow_hard_cases"]))
    print(_table_row("Rule-only baseline", summary["rule_only_yellow_hard_cases"]))


if __name__ == "__main__":
    main()