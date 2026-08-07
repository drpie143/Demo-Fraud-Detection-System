import os
import sys
import json
import random
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

import pandas as pd

from evaluation.benchmark_seed import _risk_from_behaviour, behavioural_risk_score

random.seed(42)

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

def load_csv():
    csv_path = project_root / "final.csv"
    if not csv_path.exists():
        print(f"ERROR: cannot find {csv_path}")
        sys.exit(1)

    df = pd.read_csv(csv_path)
    print(f"Loaded {len(df)} rows from final.csv")
    return df

def build_profiles(df: pd.DataFrame) -> list[dict]:
    profiles = []
    seen = set()

    first_names = [
        "An", "Bình", "Châu", "Dũng", "Hà", "Khánh", "Linh", "Minh",
        "Nam", "Phương", "Quân", "Sơn", "Thảo", "Uyên", "Vinh", "Yến",
        "Đức", "Hương", "Lan", "Tùng", "Mai", "Hải", "Trang", "Long",
        "Ngọc", "Tuấn", "Hoa", "Trung", "Thúy", "Khoa", "Duy", "Hằng",
    ]
    last_names = [
        "Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Phan",
        "Vũ", "Võ", "Đặng", "Bùi", "Đỗ", "Hồ", "Ngô", "Dương", "Lý",
    ]

    def gen_name(account_id: str) -> str:
        h = int(hashlib.sha256(account_id.encode("utf-8")).hexdigest(), 16)
        last = last_names[abs(h) % len(last_names)]
        first = first_names[abs(h >> 8) % len(first_names)]
        middle = first_names[abs(h >> 16) % len(first_names)]
        return f"{last} {middle} {first}"

    # Build sender profiles
    sender_grp = df.groupby("sender_account_no")
    for acc_id, grp in sender_grp:
        if acc_id in seen:
            continue
        seen.add(acc_id)

        # Behavioural, not label-derived. Deriving risk from the account's
        # historical isFraud rate handed the investigation agents the answer for
        # every account they had seen before.
        behaviour_score = behavioural_risk_score(grp)
        risk = _risk_from_behaviour(behaviour_score)
        avg_amount = grp["amount"].mean()
        txn_count = len(grp)

        type_mode = grp["type"].mode().iloc[0] if len(grp) > 0 else "TRANSFER"
        account_type = {
            "TRANSFER": "checking",
            "CASH_OUT": "checking",
            "PAYMENT": "personal",
            "CASH_IN": "savings",
            "DEBIT": "personal",
        }.get(type_mode, "checking")

        last_row = grp.iloc[-1]
        typical_channels = list(grp["type"].unique())

        if risk in ("critical", "high"):
            age_days = random.randint(10, 90)
        elif risk == "medium":
            age_days = random.randint(90, 365)
        else:
            age_days = random.randint(365, 2000)

        profiles.append({
            "_id": acc_id,
            "customer_id": acc_id,
            "name": gen_name(acc_id),
            "account_type": account_type,
            "account_age_days": age_days,
            "avg_monthly_transactions": max(1, txn_count),
            "avg_transaction_amount": round(avg_amount, 2),
            "typical_channels": typical_channels[:3],
            "typical_locations": ["Ho Chi Minh City"],
            "risk_category": risk,
            "device_id": str(last_row.get("device_id", "")),
            "ip_address": str(last_row.get("ip_address", "")),
            "geolocation": {
                "lat": float(last_row["geolocation_lat"]) if pd.notna(last_row.get("geolocation_lat")) else None,
                "long": float(last_row["geolocation_long"]) if pd.notna(last_row.get("geolocation_long")) else None,
            },
            "behaviour_risk_score": behaviour_score,
        })

    # Build receiver profiles
    for acc_id in df["receiver_account_no"].unique():
        if acc_id in seen:
            continue
        seen.add(acc_id)

        recv_txns = df[df["receiver_account_no"] == acc_id]
        # Receivers have no sending behaviour, so risk comes from how much
        # inbound value they concentrate: a mule collects from many senders.
        inbound = len(recv_txns)
        distinct_senders = int(recv_txns["sender_account_no"].nunique()) if inbound else 0
        behaviour_score = round(min(0.95, 0.05 + 0.12 * distinct_senders + 0.04 * inbound), 3)
        risk = _risk_from_behaviour(behaviour_score)

        profiles.append({
            "_id": acc_id,
            "customer_id": acc_id,
            "name": gen_name(acc_id),
            "account_type": "checking",
            "account_age_days": random.randint(30, 1500),
            "avg_monthly_transactions": 0,
            "avg_transaction_amount": 0.0,
            "typical_channels": [],
            "typical_locations": ["Ho Chi Minh City"],
            "risk_category": risk,
            "device_id": None,
            "ip_address": None,
            "geolocation": None,
            "behaviour_risk_score": behaviour_score,
        })

    return profiles

def build_transactions(df: pd.DataFrame) -> list[dict]:
    now = datetime.now()
    transactions = []

    for idx, row in df.iterrows():
        ts = now - timedelta(
            days=random.randint(0, 30),
            hours=random.randint(0, 23),
            minutes=random.randint(0, 59),
        )

        txn = {
            "account_id": row["sender_account_no"],
            "transaction_id": f"TXN_{idx:05d}",
            "timestamp": ts.isoformat(),
            "amount": float(row["amount"]),
            "receiver_id": row["receiver_account_no"],
            "type": row["type"],
            "channel": random.choice(["mobile", "web"]),
            "device_id": str(row["device_id"]) if pd.notna(row.get("device_id")) else None,
            "ip_address": str(row["ip_address"]) if pd.notna(row.get("ip_address")) else None,
            "geolocation": {
                "lat": float(row["geolocation_lat"]) if pd.notna(row.get("geolocation_lat")) else None,
                "long": float(row["geolocation_long"]) if pd.notna(row.get("geolocation_long")) else None,
            },
            "sender_balance_before": float(row["sender_balance_before"]) if pd.notna(row.get("sender_balance_before")) else None,
            "sender_balance_after": float(row["sender_balance_after"]) if pd.notna(row.get("sender_balance_after")) else None,
        }
        transactions.append(txn)

    return transactions

def main():
    print("STEP 1: build dataset seed data from final.csv")
    df = load_csv()
    
    profiles = build_profiles(df)
    transactions = build_transactions(df)
    
    # Save the dataframe records to json too so push script can calculate edges
    # For edges, we just need basic sender, receiver, device, ip, amount
    edges_data = df[["sender_account_no", "receiver_account_no", "amount", "device_id", "ip_address"]].copy()
    
    # Fill NA using empty string for ip and device before to_dict
    edges_data["device_id"] = edges_data["device_id"].fillna("")
    edges_data["ip_address"] = edges_data["ip_address"].fillna("")
    
    edges_records = edges_data.to_dict("records")
    
    out_dir = project_root / "evaluation" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    out_file = out_dir / "processed_seed_data.json"
    
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "profiles": profiles,
            "transactions": transactions,
            "raw_edges": edges_records
        }, f, ensure_ascii=False, indent=2)
        
    print(f"Built {len(profiles)} profiles and {len(transactions)} transactions.")
    print(f"Saved result to: {out_file}")
    print('Next: python evaluation/push_seed_data.py')

if __name__ == "__main__":
    main()
