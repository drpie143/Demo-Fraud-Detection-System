from __future__ import annotations

import hashlib
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

import pandas as pd


def _stable_name(account_id: str) -> str:
    first_names = [
        "An", "Binh", "Chau", "Dung", "Ha", "Khanh", "Linh", "Minh",
        "Nam", "Phuong", "Quan", "Son", "Thao", "Uyen", "Vinh", "Yen",
        "Duc", "Huong", "Lan", "Tung", "Mai", "Hai", "Trang", "Long",
        "Ngoc", "Tuan", "Hoa", "Trung", "Thuy", "Khoa", "Duy", "Hang",
    ]
    last_names = [
        "Nguyen", "Tran", "Le", "Pham", "Hoang", "Huynh", "Phan",
        "Vu", "Vo", "Dang", "Bui", "Do", "Ho", "Ngo", "Duong", "Ly",
    ]
    h = int(hashlib.sha256(account_id.encode("utf-8")).hexdigest(), 16)
    return f"{last_names[h % len(last_names)]} {first_names[(h >> 8) % len(first_names)]}"


def _risk_from_ratio(fraud_ratio: float, *, receiver: bool = False) -> str:
    if receiver:
        if fraud_ratio >= 0.7:
            return "high"
        if fraud_ratio >= 0.3:
            return "medium"
        return "low"

    if fraud_ratio >= 0.7:
        return "critical"
    if fraud_ratio >= 0.4:
        return "high"
    if fraud_ratio >= 0.1:
        return "medium"
    return "low"


def build_seed_from_dataframe(df: pd.DataFrame, *, seed: int = 42) -> dict[str, list[dict]]:
    """Build DB seed data from a train split only.

    Labels in this seed are allowed to summarize only the train split. The holdout
    rows must not be included here, otherwise Phase 1 can learn the answer.
    """

    rng = random.Random(seed)
    profiles: list[dict] = []
    seen: set[str] = set()

    for acc_id, grp in df.groupby("sender_account_no"):
        seen.add(str(acc_id))
        fraud_ratio = float(grp["isFraud"].mean())
        risk = _risk_from_ratio(fraud_ratio)
        last_row = grp.iloc[-1]
        type_mode = grp["type"].mode().iloc[0] if len(grp) else "TRANSFER"
        account_type = {
            "TRANSFER": "checking",
            "CASH_OUT": "checking",
            "PAYMENT": "personal",
            "CASH_IN": "savings",
            "DEBIT": "personal",
        }.get(type_mode, "checking")
        if risk in {"critical", "high"}:
            age_days = rng.randint(10, 90)
        elif risk == "medium":
            age_days = rng.randint(90, 365)
        else:
            age_days = rng.randint(365, 2000)

        profiles.append({
            "_id": str(acc_id),
            "customer_id": str(acc_id),
            "name": _stable_name(str(acc_id)),
            "account_type": account_type,
            "account_age_days": age_days,
            "avg_monthly_transactions": int(len(grp)),
            "avg_transaction_amount": round(float(grp["amount"].mean()), 2),
            "typical_channels": list(grp["type"].dropna().unique())[:3],
            "typical_locations": ["Ho Chi Minh City"],
            "risk_category": risk,
            "device_id": _maybe_str(last_row.get("device_id")),
            "ip_address": _maybe_str(last_row.get("ip_address")),
            "geolocation": {
                "lat": _maybe_float(last_row.get("geolocation_lat")),
                "long": _maybe_float(last_row.get("geolocation_long")),
            },
            "fraud_ratio": round(fraud_ratio, 2),
        })

    for acc_id in df["receiver_account_no"].dropna().unique():
        acc_id = str(acc_id)
        if acc_id in seen:
            continue
        recv_txns = df[df["receiver_account_no"] == acc_id]
        fraud_ratio = float(recv_txns["isFraud"].mean()) if len(recv_txns) else 0.0
        profiles.append({
            "_id": acc_id,
            "customer_id": acc_id,
            "name": _stable_name(acc_id),
            "account_type": "checking",
            "account_age_days": rng.randint(30, 1500),
            "avg_monthly_transactions": 0,
            "avg_transaction_amount": 0.0,
            "typical_channels": [],
            "typical_locations": ["Ho Chi Minh City"],
            "risk_category": _risk_from_ratio(fraud_ratio, receiver=True),
            "device_id": None,
            "ip_address": None,
            "geolocation": None,
            "fraud_ratio": round(fraud_ratio, 2),
        })

    now = datetime.now()
    transactions: list[dict] = []
    for idx, row in df.reset_index(drop=True).iterrows():
        timestamp = now - timedelta(
            days=rng.randint(0, 30),
            hours=rng.randint(0, 23),
            minutes=rng.randint(0, 59),
        )
        transactions.append({
            "account_id": str(row["sender_account_no"]),
            "transaction_id": f"TXN_TRAIN_{idx:05d}",
            "timestamp": timestamp.isoformat(),
            "amount": float(row["amount"]),
            "receiver_id": str(row["receiver_account_no"]),
            "type": str(row["type"]),
            "channel": rng.choice(["mobile", "web"]),
            "device_id": _maybe_str(row.get("device_id")),
            "ip_address": _maybe_str(row.get("ip_address")),
            "geolocation": {
                "lat": _maybe_float(row.get("geolocation_lat")),
                "long": _maybe_float(row.get("geolocation_long")),
            },
            "sender_balance_before": _maybe_float(row.get("sender_balance_before")),
            "sender_balance_after": _maybe_float(row.get("sender_balance_after")),
            "receiver_balance_before": _maybe_float(row.get("oldbalanceDest")),
            "receiver_balance_after": _maybe_float(row.get("newbalanceDest")),
        })

    raw_edges = []
    for _, row in df.iterrows():
        raw_edges.append({
            "sender_account_no": str(row["sender_account_no"]),
            "receiver_account_no": str(row["receiver_account_no"]),
            "amount": float(row["amount"]),
            "device_id": _maybe_str(row.get("device_id")),
            "ip_address": _maybe_str(row.get("ip_address")),
        })

    return {"profiles": profiles, "transactions": transactions, "raw_edges": raw_edges}


def apply_seed_to_simulators(data: dict[str, list[dict]], *, redis_service=None) -> dict[str, int]:
    from infrastructure.databases.simulators import dynamodb_sim, neptune_sim, redis_sim

    profiles = data.get("profiles", [])
    transactions = data.get("transactions", [])
    raw_edges = data.get("raw_edges", [])

    profile_by_id = {p["customer_id"]: dict(p) for p in profiles if p.get("customer_id")}
    tx_by_account: dict[str, list[dict]] = defaultdict(list)
    for txn in transactions:
        account_id = txn.get("account_id")
        if account_id:
            tx_by_account[account_id].append(dict(txn))
    for account_txns in tx_by_account.values():
        account_txns.sort(key=lambda item: item.get("timestamp", ""), reverse=True)

    dynamodb_sim._profiles = profile_by_id
    dynamodb_sim._transactions = dict(tx_by_account)

    nodes: dict[str, dict] = {}
    edges: list[tuple[str, str, str, dict]] = []
    for profile in profiles:
        account_id = profile.get("customer_id")
        if account_id:
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
        amount = float(raw.get("amount") or 0)
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
        edges.append((sender, device_id, "uses_device", {"since": "train"}))
    for sender, ip_address in ip_edges:
        edges.append((sender, ip_address, "connects_from", {"frequency": "train"}))

    neptune_sim._nodes = nodes
    neptune_sim._edges = edges

    sender_counts = Counter(str(raw.get("sender_account_no") or "") for raw in raw_edges)
    low_risk = {
        p["customer_id"]
        for p in profiles
        if p.get("customer_id")
        and p.get("risk_category") == "low"
        and float(p.get("avg_monthly_transactions") or 0) > 0
    }
    blacklisted = {
        p["customer_id"]
        for p in profiles
        if p.get("customer_id")
        and (p.get("risk_category") == "critical" or float(p.get("fraud_ratio") or 0) >= 0.8)
    }
    risk_map = {"low": 0.08, "medium": 0.45, "high": 0.75, "critical": 0.95}
    risk_scores = {}
    for profile in profiles:
        account_id = profile.get("customer_id")
        if not account_id:
            continue
        fraud_ratio = float(profile.get("fraud_ratio") or 0)
        base = risk_map.get(profile.get("risk_category"), 0.3)
        risk_scores[account_id] = round(max(base, min(0.98, 0.2 + fraud_ratio * 0.78)), 3)

    now = datetime.now()
    velocity = {}
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

    return {"profiles": len(profiles), "transactions": len(transactions), "edges": len(raw_edges)}


def _maybe_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _maybe_str(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value)
