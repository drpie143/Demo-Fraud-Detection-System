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


def behavioural_risk_score(grp: pd.DataFrame) -> float:
    """Score an account from how it behaves, not from whether it was fraud.

    The seed used to set an account's risk directly from its historical
    `isFraud` rate. That is temporally clean -- it only reads the train split --
    but it hands the investigation agents a copy of the answer for any account
    they have seen before: on the holdout, "this sender has prior fraud" alone
    predicted fraud with 0.97 precision.

    A bank does not know the label either. It scores accounts on observable
    behaviour, which correlates with fraud without being it.
    """
    n = len(grp)
    if n == 0:
        return 0.1

    velocity = min(n / 8.0, 1.0)
    drained = float((pd.to_numeric(grp["sender_balance_after"], errors="coerce") <= 0).mean())
    risky_types = float(grp["type"].isin(["TRANSFER", "CASH_OUT"]).mean())

    amounts = pd.to_numeric(grp["amount"], errors="coerce")
    balances = pd.to_numeric(grp["sender_balance_before"], errors="coerce").replace(0, pd.NA)
    ratio = (amounts / balances).dropna()
    drain_ratio = float(ratio.clip(0, 1).mean()) if len(ratio) else 0.0

    device_churn = min(grp["device_id"].nunique() / 4.0, 1.0) if "device_id" in grp else 0.0
    ip_churn = min(grp["ip_address"].nunique() / 4.0, 1.0) if "ip_address" in grp else 0.0
    weak_auth = (
        float((grp["auth_method"] == "SMS_OTP").mean()) if "auth_method" in grp else 0.0
    )

    score = (
        0.22 * velocity
        + 0.20 * drained
        + 0.14 * risky_types
        + 0.16 * drain_ratio
        + 0.12 * device_churn
        + 0.08 * ip_churn
        + 0.08 * weak_auth
    )
    return round(min(0.98, max(0.02, score)), 3)


def _risk_from_behaviour(score: float) -> str:
    if score >= 0.62:
        return "critical"
    if score >= 0.48:
        return "high"
    if score >= 0.32:
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
        # Risk comes from behaviour. The historical fraud rate is still recorded
        # for reporting, but nothing downstream is allowed to score on it.
        behaviour_score = behavioural_risk_score(grp)
        fraud_ratio = float(grp["isFraud"].mean())
        risk = _risk_from_behaviour(behaviour_score)
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
            "behaviour_risk_score": behaviour_score,
            # Reporting only. Nothing downstream may score on this: it is the
            # label, and using it would make the agents look prescient.
            "fraud_ratio_reporting_only": round(fraud_ratio, 2),
        })

    for acc_id in df["receiver_account_no"].dropna().unique():
        acc_id = str(acc_id)
        if acc_id in seen:
            continue
        recv_txns = df[df["receiver_account_no"] == acc_id]
        fraud_ratio = float(recv_txns["isFraud"].mean()) if len(recv_txns) else 0.0
        # Receivers have no sending behaviour to score, so risk comes from how
        # much inbound value they concentrate -- a mule account collects from
        # many senders. Still not the label.
        inbound = len(recv_txns)
        distinct_senders = int(recv_txns["sender_account_no"].nunique()) if inbound else 0
        receiver_score = min(0.95, 0.05 + 0.12 * distinct_senders + 0.04 * inbound)
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
            "risk_category": _risk_from_behaviour(receiver_score),
            "device_id": None,
            "ip_address": None,
            "geolocation": None,
            "behaviour_risk_score": round(receiver_score, 3),
            "fraud_ratio_reporting_only": round(fraud_ratio, 2),
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
                "behaviour_risk_score": profile.get("behaviour_risk_score", 0),
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
    # A blacklist is a record of confirmed investigations, so deriving it from
    # train-split labels is realistic. It is deliberately narrow, and evaluation
    # reports seen-before and first-seen accounts separately, because catching a
    # known mule says nothing about what the investigation agents add.
    blacklisted = {
        p["customer_id"]
        for p in profiles
        if p.get("customer_id") and float(p.get("fraud_ratio_reporting_only") or 0) >= 0.8
    }
    # Risk scores are behavioural. Deriving them from the label would hand every
    # repeat account's answer to the agents as "evidence".
    risk_scores = {}
    for profile in profiles:
        account_id = profile.get("customer_id")
        if not account_id:
            continue
        risk_scores[account_id] = float(profile.get("behaviour_risk_score") or 0.1)

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

    # Index shared devices and addresses from the train split so Phase 1 can ask
    # "how many accounts use this device" instead of matching a demo-data prefix
    # that appears on no real row.
    infrastructure_rows = [
        (
            str(raw.get("sender_account_no") or ""),
            _maybe_str(raw.get("device_id")),
            _maybe_str(raw.get("ip_address")),
        )
        for raw in raw_edges
    ]
    for target in [redis_sim, redis_service]:
        if target is not None and hasattr(target, "register_infrastructure"):
            target.register_infrastructure(infrastructure_rows)

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
