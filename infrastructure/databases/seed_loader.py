from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core.schemas.models import Transaction


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DATA_PATH = PROJECT_ROOT / "evaluation" / "data" / "processed_seed_data.json"
FINAL_CSV_PATH = PROJECT_ROOT / "final.csv"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def load_processed_seed_data() -> dict[str, list[dict]]:
    if not PROCESSED_DATA_PATH.exists():
        return {"profiles": [], "transactions": [], "raw_edges": []}

    with PROCESSED_DATA_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    return {
        "profiles": data.get("profiles", []),
        "transactions": data.get("transactions", []),
        "raw_edges": data.get("raw_edges", []),
    }


def load_final_csv_rows() -> list[dict[str, str]]:
    if not FINAL_CSV_PATH.exists():
        return []

    with FINAL_CSV_PATH.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _profile_map() -> dict[str, dict]:
    data = load_processed_seed_data()
    return {p.get("customer_id", ""): p for p in data.get("profiles", [])}


def transaction_from_row(row: dict[str, Any], profiles: dict[str, dict] | None = None) -> Transaction:
    profiles = profiles or {}
    sender_id = str(row.get("sender_account_no") or row.get("account_id") or "")
    receiver_id = str(row.get("receiver_account_no") or row.get("receiver_id") or "")
    sender_profile = profiles.get(sender_id, {})
    receiver_profile = profiles.get(receiver_id, {})
    step = str(row.get("step", row.get("transaction_id", "DATA")))
    txn_id = str(row.get("transaction_id") or f"TXN_DATA_{step}_{sender_id}_{receiver_id}")

    timestamp = row.get("timestamp") or datetime.now().isoformat()

    return Transaction(
        transaction_id=txn_id,
        timestamp=str(timestamp),
        sender_id=sender_id,
        sender_name=sender_profile.get("name", sender_id),
        sender_account_type=sender_profile.get("account_type", "checking"),
        receiver_id=receiver_id,
        receiver_name=receiver_profile.get("name", receiver_id),
        amount=_safe_float(row.get("amount")),
        currency="VND",
        transaction_type=str(row.get("type", "TRANSFER")).upper(),
        device_id=str(row.get("device_id") or sender_profile.get("device_id") or ""),
        ip_address=str(row.get("ip_address") or sender_profile.get("ip_address") or ""),
        channel=str(row.get("channel") or row.get("type") or "web").lower(),
        geolocation_lat=_safe_float(row.get("geolocation_lat"), None),
        geolocation_long=_safe_float(row.get("geolocation_long"), None),
        sender_balance_before=_safe_float(row.get("sender_balance_before"), None),
        sender_balance_after=_safe_float(row.get("sender_balance_after"), None),
        receiver_balance_before=_safe_float(row.get("oldbalanceDest"), None),
        receiver_balance_after=_safe_float(row.get("newbalanceDest"), None),
        description=str(row.get("description") or f"{row.get('type', 'TRANSFER')} from dataset"),
        auth_method=str(row.get("auth_method") or ""),
        expected_is_fraud=bool(_safe_int(row.get("isFraud"), 0)),
    )


def _find_row(rows: list[dict[str, str]], sender: str, receiver: str, amount: float) -> dict[str, str] | None:
    for row in rows:
        if (
            row.get("sender_account_no") == sender
            and row.get("receiver_account_no") == receiver
            and abs(_safe_float(row.get("amount")) - amount) < 0.01
        ):
            return row
    return None


def build_demo_scenarios() -> list[dict]:
    rows = load_final_csv_rows()
    profiles = _profile_map()
    scenarios: list[dict] = []

    specs = [
        (
            "Clean small payment",
            "Low-risk payment from the dataset. Expected ALLOW.",
            "C8126703807",
            "C1409103719",
            144.88,
            "allow",
        ),
        (
            "Fraud cluster transfer",
            "Known fraudulent sender in the dense transfer cluster. Expected BLOCK.",
            "C2972777054",
            "C8992641070",
            27000.0,
            "block",
        ),
        (
            "Second fraud cluster",
            "Another fraud-cluster transfer with shared infrastructure. Expected BLOCK.",
            "C2006456468",
            "C3259274595",
            22000.0,
            "block",
        ),
    ]

    for name, description, sender, receiver, amount, expected in specs:
        row = _find_row(rows, sender, receiver, amount)
        if row:
            scenarios.append(
                {
                    "name": name,
                    "description": description,
                    "expected_decision": expected,
                    "transaction": transaction_from_row(row, profiles),
                }
            )

    high_value_clean = None
    for row in sorted(rows, key=lambda r: _safe_float(r.get("amount")), reverse=True):
        if _safe_int(row.get("isFraud")) == 0:
            high_value_clean = row
            break

    if high_value_clean:
        scenarios.append(
            {
                "name": "High-value legitimate transfer",
                "description": "Large non-fraud transaction. Expected not to auto-block solely due to amount.",
                "expected_decision": "allow",
                "transaction": transaction_from_row(high_value_clean, profiles),
            }
        )

    if scenarios:
        return scenarios

    # Emergency fallback if the CSV is missing.
    return [
        {
            "name": "Fallback clean transaction",
            "description": "Fallback scenario used only when dataset files are unavailable.",
            "expected_decision": "allow",
            "transaction": Transaction(
                transaction_id="TXN_FALLBACK_CLEAN",
                timestamp=datetime.now().isoformat(),
                sender_id="C8126703807",
                receiver_id="C1409103719",
                amount=144.88,
                currency="VND",
                transaction_type="PAYMENT",
            ),
        }
    ]


def dataset_summary() -> dict[str, Any]:
    data = load_processed_seed_data()
    rows = load_final_csv_rows()
    fraud_counts = Counter(_safe_int(row.get("isFraud")) for row in rows)
    return {
        "profiles": len(data.get("profiles", [])),
        "transactions": len(data.get("transactions", [])),
        "raw_edges": len(data.get("raw_edges", [])),
        "csv_rows": len(rows),
        "fraud": fraud_counts.get(1, 0),
        "non_fraud": fraud_counts.get(0, 0),
    }


def apply_processed_seed_to_simulators(
    *,
    redis_service: Any | None = None,
    redis_sim: Any | None = None,
    dynamodb_sim: Any | None = None,
    neptune_sim: Any | None = None,
) -> dict[str, int]:
    data = load_processed_seed_data()
    profiles = data.get("profiles", [])
    transactions = data.get("transactions", [])
    raw_edges = data.get("raw_edges", [])

    if not profiles or not raw_edges:
        return {"profiles": 0, "transactions": 0, "edges": 0}

    profile_by_id = {p["customer_id"]: dict(p) for p in profiles if p.get("customer_id")}

    tx_by_account: dict[str, list[dict]] = defaultdict(list)
    for txn in transactions:
        account_id = txn.get("account_id")
        if account_id:
            tx_by_account[account_id].append(dict(txn))

    for account_txns in tx_by_account.values():
        account_txns.sort(key=lambda item: item.get("timestamp", ""), reverse=True)

    if dynamodb_sim is not None:
        dynamodb_sim._profiles = profile_by_id
        dynamodb_sim._transactions = dict(tx_by_account)

    if neptune_sim is not None:
        nodes: dict[str, dict] = {}
        edges: list[tuple[str, str, str, dict]] = []

        for profile in profiles:
            account_id = profile.get("customer_id")
            if not account_id:
                continue
            nodes[account_id] = {
                "type": "account",
                "label": profile.get("name", account_id),
                "risk": profile.get("risk_category", "unknown"),
                "fraud_ratio": profile.get("fraud_ratio", 0),
            }

        transfer_map: dict[tuple[str, str], dict[str, float | int]] = defaultdict(
            lambda: {"total_amount": 0.0, "count": 0}
        )
        device_edges = set()
        ip_edges = set()

        for raw in raw_edges:
            sender = str(raw.get("sender_account_no") or "")
            receiver = str(raw.get("receiver_account_no") or "")
            amount = _safe_float(raw.get("amount"))
            if sender and receiver:
                transfer_map[(sender, receiver)]["total_amount"] += amount
                transfer_map[(sender, receiver)]["count"] += 1
            device_id = str(raw.get("device_id") or "")
            ip_address = str(raw.get("ip_address") or "")
            if sender and device_id:
                nodes[device_id] = {"type": "device", "label": f"Device-{device_id[:8]}"}
                device_edges.add((sender, device_id))
            if sender and ip_address:
                nodes[ip_address] = {"type": "ip", "label": ip_address}
                ip_edges.add((sender, ip_address))

        for (sender, receiver), meta in transfer_map.items():
            edges.append((sender, receiver, "transfers_to", meta))
        for sender, device_id in device_edges:
            edges.append((sender, device_id, "uses_device", {"since": "dataset"}))
        for sender, ip_address in ip_edges:
            edges.append((sender, ip_address, "connects_from", {"frequency": "dataset"}))

        neptune_sim._nodes = nodes
        neptune_sim._edges = edges

    sender_counts = Counter(str(raw.get("sender_account_no") or "") for raw in raw_edges)
    low_risk = {
        p["customer_id"]
        for p in profiles
        if p.get("customer_id")
        and p.get("risk_category") == "low"
        and _safe_float(p.get("avg_monthly_transactions")) > 0
    }
    blacklisted = {
        p["customer_id"]
        for p in profiles
        if p.get("customer_id")
        and (p.get("risk_category") == "critical" or _safe_float(p.get("fraud_ratio")) >= 0.8)
    }
    risk_map = {"low": 0.08, "medium": 0.45, "high": 0.75, "critical": 0.95}
    risk_scores = {}
    for profile in profiles:
        account_id = profile.get("customer_id")
        if not account_id:
            continue
        fraud_ratio = _safe_float(profile.get("fraud_ratio"))
        base = risk_map.get(profile.get("risk_category"), 0.3)
        risk_scores[account_id] = round(max(base, min(0.98, 0.2 + fraud_ratio * 0.78)), 3)

    velocity = {}
    now = datetime.now()
    for account_id, count in sender_counts.items():
        if account_id and count > 1:
            velocity[account_id] = [
                (now - timedelta(minutes=i * 4)).isoformat()
                for i in range(min(count, 20))
            ]

    redis_targets = [redis_sim]
    if redis_service is not None and getattr(redis_service, "_mode", "") == "simulator":
        redis_targets.append(getattr(redis_service, "_simulator", None))

    for target in redis_targets:
        if target is None:
            continue
        target._whitelist = set(low_risk)
        target._blacklist = set(blacklisted)
        target._risk_scores = dict(risk_scores)
        target._velocity = dict(velocity)

    return {
        "profiles": len(profiles),
        "transactions": len(transactions),
        "edges": len(raw_edges),
    }
