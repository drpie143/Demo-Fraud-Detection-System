"""Dataset-first setup helpers.

This module used to contain a synthetic ACC_* demo seed. It is now kept as a
small compatibility layer because `evaluation/push_seed_data.py` imports
`CHROMA_DOCUMENTS` from here.

For data preparation use:
    python evaluation/build_seed_data.py
    python evaluation/push_seed_data.py
"""

from __future__ import annotations


CHROMA_DOCUMENTS = [
    {
        "id": "pattern_structuring_dataset",
        "text": (
            "Structuring pattern: repeated transfers near configured thresholds, "
            "especially when sender velocity is high, receivers vary, or the sender "
            "drains most of the available balance. In the local dataset this should "
            "be evaluated with VND-scale thresholds, not small USD constants."
        ),
        "metadata": {
            "type": "fraud_pattern",
            "risk_level": "high",
            "confidence_boost": 0.25,
            "title": "Dataset Structuring Pattern",
        },
    },
    {
        "id": "pattern_mule_cluster_dataset",
        "text": (
            "Money mule cluster pattern: fraud senders and receivers form dense "
            "transfer clusters with repeated device or IP reuse. Example dataset "
            "transactions include C2972777054 to C8992641070 and C2006456468 to "
            "C3259274595, both expected to block in the demo scenarios."
        ),
        "metadata": {
            "type": "fraud_pattern",
            "risk_level": "critical",
            "confidence_boost": 0.35,
            "title": "Dataset Mule Cluster Pattern",
        },
    },
    {
        "id": "pattern_balance_drain_dataset",
        "text": (
            "Balance drain pattern: sender balance falls to near zero after a "
            "transfer, or the transfer amount represents most of the sender balance. "
            "This signal is strong when combined with high fraud ratio, repeated "
            "recent drains, blacklist hits, or suspicious graph neighbors."
        ),
        "metadata": {
            "type": "fraud_pattern",
            "risk_level": "high",
            "confidence_boost": 0.3,
            "title": "Balance Drain Pattern",
        },
    },
    {
        "id": "pattern_legit_high_value_dataset",
        "text": (
            "High-value legitimate transfer pattern: large amount alone should not "
            "force a block. The system should escalate or investigate when needed, "
            "then allow if profile, graph, velocity, and evidence do not show material "
            "fraud indicators."
        ),
        "metadata": {
            "type": "mitigating_pattern",
            "risk_level": "medium",
            "confidence_boost": -0.1,
            "title": "Legitimate High-Value Transfer",
        },
    },
    {
        "id": "rule_dataset_thresholds",
        "text": (
            "Dataset screening rule: use configured VND-like thresholds. Defaults are "
            "instant_allow_max=5000, elevated_amount_threshold=5000, "
            "large_amount_threshold=1000000, suspicious_velocity_threshold=5, "
            "high_risk_threshold=0.6, red_risk_threshold=0.9."
        ),
        "metadata": {
            "type": "screening_rule",
            "title": "Dataset-Scale Thresholds",
            "threshold_profile": "dataset_vnd",
        },
    },
]


def main() -> None:
    print("Dataset setup is split into two commands:")
    print("  python evaluation/build_seed_data.py")
    print("  python evaluation/push_seed_data.py")
    print("This compatibility module only exports CHROMA_DOCUMENTS.")


if __name__ == "__main__":
    main()
