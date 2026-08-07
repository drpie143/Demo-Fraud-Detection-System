"""Rebuild the enriched dataset so no generated column gives the label away.

What the previous dataset looked like
-------------------------------------
`auth_method == 'SMS_OTP'` covered 179 of 202 fraud rows with zero false
positives. Any account with more than two transactions was fraudulent. Device
and IP reuse counts above two were fraudulent. A two-line rule scored a perfect
F1 on the published holdout, which makes every model result on that data
meaningless.

The cause was the order of generation: the label was decided first, then the
enrichment was written to look guilty. Real banking data works the other way --
most weak-auth transactions are legitimate, most shared devices are households.

What this script does
---------------------
1. Preserves every PaySim column and every one of the 202 real fraud rows.
2. Adds legitimate transaction history so that transacting repeatedly stops
   being diagnostic, and gives some fraud accounts a clean history first, which
   is what account takeover actually looks like.
3. Regenerates device, IP, geolocation and authentication from account
   behaviour and bank policy. The label shifts probabilities; it never decides
   a value.

Every added row carries `row_source=synthetic_activity`; PaySim rows keep
`row_source=paysim`.

Usage:
    python evaluation/regenerate_dataset.py
    python evaluation/regenerate_dataset.py --seed 42 --output final.csv
"""

from __future__ import annotations

import argparse
import random
import uuid
from collections import defaultdict
from pathlib import Path

import pandas as pd

PAYSIM_COLUMNS = [
    "step",
    "type",
    "amount",
    "sender_account_no",
    "sender_balance_before",
    "sender_balance_after",
    "receiver_account_no",
    "oldbalanceDest",
    "newbalanceDest",
    "isFraud",
    "isFlaggedFraud",
]
GENERATED_COLUMNS = [
    "device_id",
    "ip_address",
    "geolocation_lat",
    "geolocation_long",
    "auth_method",
]

# --- Authentication policy -------------------------------------------------
# A bank picks authentication strength from the transaction's own risk, before
# knowing whether it is fraud. Attackers then prefer the weakest channel
# available, which is a tilt, not a rule.
AUTH_BY_AMOUNT_TIER = {
    "small": {"SMS_OTP": 0.50, "SMART_OTP": 0.34, "FACE_ID": 0.16},
    "medium": {"SMS_OTP": 0.28, "SMART_OTP": 0.44, "FACE_ID": 0.28},
    "large": {"SMS_OTP": 0.12, "SMART_OTP": 0.45, "FACE_ID": 0.43},
}
SMALL_AMOUNT = 50_000
LARGE_AMOUNT = 500_000
FRAUD_WEAK_AUTH_TILT = 0.14  # probability mass moved to SMS_OTP for fraud rows

# --- Device and network behaviour -----------------------------------------
P_NEW_DEVICE = {0: 0.10, 1: 0.34}       # legitimate vs fraud
P_NEW_IP = {0: 0.18, 1: 0.42}
# Shared infrastructure has to stay a usable signal: finding it is what the
# graph agent exists to do. Mule rings are few and large, households are many
# and small, so "many accounts on one device" leans fraudulent without ever
# proving it.
P_LEGIT_ACCOUNT_SHARES_DEVICE = 0.13    # households, shared office machines
P_FRAUD_ACCOUNT_IN_RING = 0.62          # mule rings sharing infrastructure
RING_COUNT = 6
SHARED_HOUSEHOLD_COUNT = 34
# An account that belongs to shared infrastructure has to actually use it,
# otherwise the shared device never appears in the data and the graph agent has
# nothing to find.
P_USE_SHARED_DEVICE = 0.72
P_USE_SHARED_IP = 0.66

# --- Account history -------------------------------------------------------
TARGET_MULTI_TXN_LEGIT_ACCOUNTS = 110   # accounts given 3+ transactions
EXTRA_TXN_RANGE = (1, 6)
P_FRAUD_ACCOUNT_GETS_CLEAN_HISTORY = 0.35

VN_REGIONS = [
    (21.028, 105.804),   # Ha Noi
    (10.823, 106.630),   # TP HCM
    (16.047, 108.206),   # Da Nang
    (20.844, 106.688),   # Hai Phong
    (10.045, 105.746),   # Can Tho
    (12.239, 109.196),   # Nha Trang
    (11.940, 108.458),   # Da Lat
    (18.679, 105.681),   # Vinh
]


def amount_tier(amount: float) -> str:
    if amount < SMALL_AMOUNT:
        return "small"
    if amount < LARGE_AMOUNT:
        return "large" if amount >= LARGE_AMOUNT else "medium"
    return "large"


def pick_auth(rng: random.Random, amount: float, is_fraud: bool) -> str:
    weights = dict(AUTH_BY_AMOUNT_TIER[amount_tier(amount)])
    if is_fraud:
        # Move mass toward the weakest channel without ever forcing it.
        moved = 0.0
        for key in ("SMART_OTP", "FACE_ID"):
            take = weights[key] * FRAUD_WEAK_AUTH_TILT / 0.5
            weights[key] -= take
            moved += take
        weights["SMS_OTP"] += moved
    options, probs = zip(*weights.items(), strict=True)
    return rng.choices(options, weights=probs, k=1)[0]


def random_ip(rng: random.Random) -> str:
    return ".".join(str(rng.randint(1, 254)) for _ in range(4))


def jitter_location(rng: random.Random, base: tuple[float, float], spread: float) -> tuple[float, float]:
    return (
        round(base[0] + rng.uniform(-spread, spread), 6),
        round(base[1] + rng.uniform(-spread, spread), 6),
    )


def destination_from_template(rng: random.Random, templates: list[tuple], amount: float) -> tuple[float, float]:
    """Draw destination balances the way real legitimate rows look.

    Inventing these produced a fingerprint: every generated row satisfied
    `newbalanceDest - oldbalanceDest == amount` exactly and never had a zero
    destination balance, while real rows do so 55% and 49% of the time. Since
    every generated row was legitimate, a model could separate the classes by
    detecting the generator instead of detecting fraud.
    """
    old_dest, exact = rng.choice(templates)
    if exact:
        return round(old_dest, 2), round(old_dest + amount, 2)
    # Mirror the real inconsistency: destination balance moves by an unrelated
    # amount, or not at all.
    new_dest = old_dest if rng.random() < 0.55 else max(0.0, old_dest + amount * rng.uniform(-1.5, 1.5))
    return round(old_dest, 2), round(new_dest, 2)


def destination_templates(legit: pd.DataFrame) -> list[tuple]:
    """(oldbalanceDest, arithmetic_holds) pairs sampled from real legit rows."""
    old = pd.to_numeric(legit["oldbalanceDest"], errors="coerce").fillna(0.0)
    new = pd.to_numeric(legit["newbalanceDest"], errors="coerce").fillna(0.0)
    amt = pd.to_numeric(legit["amount"], errors="coerce").fillna(0.0)
    exact = (new - old - amt).abs() < 0.005
    return list(zip(old.tolist(), exact.tolist(), strict=True))


def build_extra_legit_rows(df: pd.DataFrame, rng: random.Random) -> list[dict]:
    """Give legitimate accounts a transaction history of their own.

    Without this, transacting more than twice is by itself proof of fraud: all
    26 accounts with 3+ rows were fraudulent.

    Added rows keep PaySim's class balance. An earlier version added only
    legitimate rows, which let a model score 1.000 F1 by detecting the
    generator: added rows were distinguishable and every one of them was
    legitimate, so "looks generated" implied "not fraud".
    """
    legit = df[df.isFraud == 0]
    per_account = legit.groupby("sender_account_no").size()
    candidates = [a for a, n in per_account.items() if n <= 2]
    rng.shuffle(candidates)
    chosen = candidates[:TARGET_MULTI_TXN_LEGIT_ACCOUNTS]

    # Sample type and amount from the legitimate empirical distribution so the
    # added rows do not shift the PaySim marginals.
    legit_types = legit["type"].tolist()
    amounts_by_type: dict[str, list[float]] = defaultdict(list)
    for t, a in zip(legit["type"], legit["amount"], strict=True):
        amounts_by_type[t].append(float(a))

    receivers = df["receiver_account_no"].unique().tolist()
    step_lo, step_hi = int(df.step.min()), int(df.step.max())
    templates = destination_templates(legit)

    rows: list[dict] = []
    for account in chosen:
        history = df[df.sender_account_no == account].sort_values("step")
        balance = float(history.iloc[-1]["sender_balance_after"])
        last_step = int(history.iloc[-1]["step"])
        for _ in range(rng.randint(*EXTRA_TXN_RANGE)):
            ttype = rng.choice(legit_types)
            amount = round(rng.choice(amounts_by_type[ttype]) * rng.uniform(0.5, 1.5), 2)
            if ttype == "CASH_IN":
                before, after = balance, balance + amount
            else:
                if balance < amount:
                    amount = round(balance * rng.uniform(0.1, 0.9), 2)
                before, after = balance, max(0.0, balance - amount)
            if amount <= 0:
                continue
            last_step = min(step_hi, last_step + rng.randint(1, 25))
            dest_old, dest_new = destination_from_template(rng, templates, amount)
            rows.append(
                {
                    "step": last_step,
                    "type": ttype,
                    "amount": amount,
                    "sender_account_no": account,
                    "sender_balance_before": round(before, 2),
                    "sender_balance_after": round(after, 2),
                    "receiver_account_no": rng.choice(receivers),
                    "oldbalanceDest": dest_old,
                    "newbalanceDest": dest_new,
                    "isFraud": 0,
                    "isFlaggedFraud": 0,
                    "row_source": "synthetic_activity",
                }
            )
            balance = after
    _ = step_lo
    return rows


def build_clean_history_rows(df: pd.DataFrame, rng: random.Random) -> list[dict]:
    """Give some fraud accounts legitimate activity before they are abused.

    455 of 456 accounts were either entirely fraudulent or entirely clean. A
    compromised account has a normal history first.
    """
    fraud_accounts = df[df.isFraud == 1]["sender_account_no"].unique().tolist()
    rng.shuffle(fraud_accounts)
    chosen = fraud_accounts[: int(len(fraud_accounts) * P_FRAUD_ACCOUNT_GETS_CLEAN_HISTORY)]

    legit = df[df.isFraud == 0]
    legit_types = [t for t in legit["type"] if t != "TRANSFER"]
    receivers = df["receiver_account_no"].unique().tolist()
    step_lo = int(df.step.min())
    templates = destination_templates(legit)

    rows: list[dict] = []
    for account in chosen:
        first = df[df.sender_account_no == account].sort_values("step").iloc[0]
        first_step = int(first["step"])
        if first_step - step_lo < 4:
            continue
        balance = float(first["sender_balance_before"])
        for _ in range(rng.randint(2, 5)):
            ttype = rng.choice(legit_types)
            amount = round(abs(rng.gauss(40_000, 30_000)) + 500, 2)
            if ttype == "CASH_IN":
                before, after = balance, balance + amount
            else:
                amount = min(amount, max(balance * 0.6, 1.0))
                before, after = balance, max(0.0, balance - amount)
            step = rng.randint(step_lo, max(step_lo + 1, first_step - 1))
            dest_old, dest_new = destination_from_template(rng, templates, amount)
            rows.append(
                {
                    "step": step,
                    "type": ttype,
                    "amount": round(amount, 2),
                    "sender_account_no": account,
                    "sender_balance_before": round(before, 2),
                    "sender_balance_after": round(after, 2),
                    "receiver_account_no": rng.choice(receivers),
                    "oldbalanceDest": dest_old,
                    "newbalanceDest": dest_new,
                    "isFraud": 0,
                    "isFlaggedFraud": 0,
                    "row_source": "synthetic_activity",
                }
            )
            balance = after
    return rows


def rebalance_added_rows(
    paysim: pd.DataFrame, added: list[dict], rng: random.Random
) -> list[dict]:
    """Give the added rows PaySim's class balance, templating from real rows.

    Detecting that a row was generated must tell a model nothing about its
    label. That holds only if generated rows carry the same fraud rate as real
    ones, so a share of them is relabelled and re-templated from real fraud
    rows: amount, transaction type and both destination balances are copied from
    an actual fraud transaction, with the sender's own balances kept intact so
    the account history stays coherent.
    """
    if not added:
        return added

    target_rate = float(paysim["isFraud"].mean())
    fraud_rows = paysim[paysim.isFraud == 1]
    if fraud_rows.empty:
        return added

    n_fraud = round(len(added) * target_rate)
    indices = list(range(len(added)))
    rng.shuffle(indices)

    templates = fraud_rows.to_dict("records")
    for i in indices[:n_fraud]:
        template = rng.choice(templates)
        row = added[i]
        amount = round(float(template["amount"]) * rng.uniform(0.85, 1.15), 2)
        row["type"] = template["type"]
        row["amount"] = amount
        row["oldbalanceDest"] = round(float(template["oldbalanceDest"]), 2)
        row["newbalanceDest"] = round(float(template["newbalanceDest"]), 2)
        # PaySim fraud drains the sender; keep that shape without breaking the
        # account's running balance.
        before = float(row["sender_balance_before"])
        row["sender_balance_after"] = 0.0 if rng.random() < 0.55 else round(max(0.0, before - amount), 2)
        row["isFraud"] = 1
    return added


def assign_enrichment(df: pd.DataFrame, rng: random.Random) -> pd.DataFrame:
    """Regenerate device, IP, geolocation and auth from behaviour, not label."""
    accounts = df["sender_account_no"].unique().tolist()
    fraud_accounts = set(df[df.isFraud == 1]["sender_account_no"])

    # Shared infrastructure exists on both sides: mule rings, and households or
    # offices where several legitimate accounts use one machine.
    rings = [[] for _ in range(RING_COUNT)]
    households = [[] for _ in range(SHARED_HOUSEHOLD_COUNT)]
    for account in accounts:
        if account in fraud_accounts:
            if rng.random() < P_FRAUD_ACCOUNT_IN_RING:
                rng.choice(rings).append(account)
        elif rng.random() < P_LEGIT_ACCOUNT_SHARES_DEVICE:
            rng.choice(households).append(account)

    shared_device_of: dict[str, str] = {}
    shared_ip_of: dict[str, str] = {}
    for group in rings + households:
        if len(group) < 2:
            continue
        device = str(uuid.UUID(int=rng.getrandbits(128)))
        ip = random_ip(rng)
        for account in group:
            shared_device_of[account] = device
            shared_ip_of[account] = ip

    # Every account keeps its own small pool of known devices and addresses,
    # held separately from any shared device so the shared one can be preferred
    # rather than diluted among personal devices.
    device_pool: dict[str, list[str]] = {}
    ip_pool: dict[str, list[str]] = {}
    home: dict[str, tuple[float, float]] = {}
    for account in accounts:
        device_pool[account] = [
            str(uuid.UUID(int=rng.getrandbits(128))) for _ in range(rng.randint(1, 3))
        ]
        ip_pool[account] = [random_ip(rng) for _ in range(rng.randint(1, 3))]
        home[account] = rng.choice(VN_REGIONS)

    devices, ips_out, lats, longs, auths = [], [], [], [], []
    for row in df.itertuples():
        account = row.sender_account_no
        is_fraud = int(row.isFraud)

        if rng.random() < P_NEW_DEVICE[is_fraud]:
            device = str(uuid.UUID(int=rng.getrandbits(128)))
            device_pool[account].append(device)  # it becomes known afterwards
        elif account in shared_device_of and rng.random() < P_USE_SHARED_DEVICE:
            device = shared_device_of[account]
        else:
            device = rng.choice(device_pool[account])
        devices.append(device)

        if rng.random() < P_NEW_IP[is_fraud]:
            ip = random_ip(rng)
            ip_pool[account].append(ip)
        elif account in shared_ip_of and rng.random() < P_USE_SHARED_IP:
            ip = shared_ip_of[account]
        else:
            ip = rng.choice(ip_pool[account])
        ips_out.append(ip)

        # Fraud drifts further from the account's usual area, but legitimate
        # travel exists too.
        spread = 0.35 if is_fraud and rng.random() < 0.45 else 0.06
        base = rng.choice(VN_REGIONS) if (is_fraud and rng.random() < 0.25) else home[account]
        lat, lon = jitter_location(rng, base, spread)
        lats.append(lat)
        longs.append(lon)

        auths.append(pick_auth(rng, float(row.amount), bool(is_fraud)))

    out = df.copy()
    out["device_id"] = devices
    out["ip_address"] = ips_out
    out["geolocation_lat"] = lats
    out["geolocation_long"] = longs
    out["auth_method"] = auths
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate the enriched dataset without leakage.")
    parser.add_argument("--input", default="final.csv")
    parser.add_argument("--output", default="final.csv")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    df = pd.read_csv(args.input)
    original_rows = len(df)
    original_fraud = int(df.isFraud.sum())
    print(f"Input: {original_rows} rows, {original_fraud} fraud")

    if "row_source" not in df:
        df["row_source"] = "paysim"
    paysim_core = df[df.row_source == "paysim"][PAYSIM_COLUMNS + ["row_source"]].copy()
    print(f"PaySim rows preserved: {len(paysim_core)}")

    extra = build_extra_legit_rows(paysim_core, rng)
    history = build_clean_history_rows(paysim_core, rng)
    print(f"Added legitimate activity : {len(extra)} rows")
    print(f"Added pre-fraud history   : {len(history)} rows")

    added = rebalance_added_rows(paysim_core, extra + history, rng)
    combined = pd.concat(
        [paysim_core, pd.DataFrame(added)], ignore_index=True
    ).sort_values("step", kind="stable").reset_index(drop=True)

    enriched = assign_enrichment(combined, rng)
    enriched = enriched[PAYSIM_COLUMNS + GENERATED_COLUMNS + ["row_source"]]

    fraud = int(enriched.isFraud.sum())
    real_fraud = int(enriched[enriched.row_source == "paysim"].isFraud.sum())
    assert real_fraud == original_fraud, f"PaySim fraud rows changed: {original_fraud} -> {real_fraud}"
    print(f"\nOutput: {len(enriched)} rows, {fraud} fraud ({fraud/len(enriched):.1%})")
    print(f"  of which real PaySim fraud rows: {real_fraud}")
    print(f"  augmented fraud rows (templated from real ones): {fraud - real_fraud}")

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    enriched.to_csv(args.output, index=False)
    print(f"Saved -> {args.output}")
    print("\nRun `python evaluation/audit_leakage.py --check` to verify the gates.")


if __name__ == "__main__":
    main()
