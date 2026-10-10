"""Guards against the dataset defects that made every published metric void.

The shipped dataset was trivially separable. `auth_method == 'SMS_OTP'` covered
179 of 202 fraud rows with no false positives, any account with more than two
transactions was fraudulent, and a two-line rule scored a perfect F1 on the
published holdout. Regenerating it fixed those, then introduced a subtler one:
added rows were detectable and all of them were legitimate, so a model could
score 1.000 F1 by recognising the generator.

These tests fail if any of that comes back.
"""

from __future__ import annotations

import collections
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "final.csv"


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    if not CSV.exists():
        pytest.skip("final.csv is not present")
    return pd.read_csv(CSV)


def rule_precision_recall(mask: list[bool], labels: list[int]) -> tuple[float, float]:
    tp = sum(1 for m, y in zip(mask, labels, strict=True) if m and y)
    fp = sum(1 for m, y in zip(mask, labels, strict=True) if m and not y)
    fn = sum(1 for m, y in zip(mask, labels, strict=True) if not m and y)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return precision, recall


# ------------------------------------------------------------------ leakage


def test_auth_method_is_not_a_label_copy(df):
    """SMS_OTP used to be 179/179 fraud. It must only shift the odds."""
    labels = [int(v) for v in df.isFraud]
    for method in df.auth_method.unique():
        precision, recall = rule_precision_recall([a == method for a in df.auth_method], labels)
        assert not (precision >= 0.98 and recall >= 0.10), (
            f"auth_method == {method!r} identifies fraud with precision {precision:.4f}"
        )


def test_transaction_count_is_not_a_label_copy(df):
    """Every account with 3+ transactions used to be fraudulent."""
    labels = [int(v) for v in df.isFraud]
    counts = collections.Counter(df.sender_account_no)
    for threshold in (2, 3, 4, 5):
        mask = [counts[a] > threshold for a in df.sender_account_no]
        precision, recall = rule_precision_recall(mask, labels)
        assert not (precision >= 0.98 and recall >= 0.10), (
            f"txn_count > {threshold} identifies fraud with precision {precision:.4f}"
        )


def test_device_and_ip_reuse_are_not_label_copies(df):
    labels = [int(v) for v in df.isFraud]
    for column in ("device_id", "ip_address"):
        counts = collections.Counter(df[column])
        accounts = collections.defaultdict(set)
        for value, account in zip(df[column], df.sender_account_no, strict=True):
            accounts[value].add(account)
        for threshold in (1, 2, 3, 5):
            for name, mask in (
                (f"{column} seen > {threshold}", [counts[v] > threshold for v in df[column]]),
                (
                    f"{column} shared by > {threshold}",
                    [len(accounts[v]) > threshold for v in df[column]],
                ),
            ):
                precision, recall = rule_precision_recall(mask, labels)
                assert not (precision >= 0.98 and recall >= 0.10), (
                    f"{name} identifies fraud with precision {precision:.4f}"
                )


def test_the_two_line_rule_that_used_to_be_perfect_no_longer_is(df):
    """`SMS_OTP or first-seen device` scored P=1.000 R=0.995 on the old data."""
    device_counts = collections.Counter(df.device_id)
    mask = [
        auth == "SMS_OTP" or device_counts[device] == 1
        for auth, device in zip(df.auth_method, df.device_id, strict=True)
    ]
    precision, _ = rule_precision_recall(mask, [int(v) for v in df.isFraud])
    assert precision < 0.6, f"the trivial rule still reaches precision {precision:.4f}"


def test_generated_rows_carry_the_same_class_balance(df):
    """Otherwise detecting the generator is enough to predict the label."""
    if "row_source" not in df or df.row_source.nunique() < 2:
        pytest.skip("dataset has a single provenance")
    rates = df.groupby("row_source").isFraud.mean()
    assert rates.max() - rates.min() < 0.05, (
        f"fraud rate differs by provenance: {rates.to_dict()}"
    )


def test_every_real_paysim_fraud_row_is_preserved(df):
    """Regeneration may add rows; it may never drop real fraud evidence."""
    if "row_source" not in df:
        pytest.skip("dataset has no provenance column")
    assert int(df[df.row_source == "paysim"].isFraud.sum()) == 202


# ------------------------------------------------- behavioural risk scoring


def test_behavioural_risk_score_ignores_the_label():
    """Risk used to be a direct function of the account's historical isFraud."""
    from evaluation.benchmark_seed import behavioural_risk_score

    base = pd.DataFrame(
        {
            "amount": [1000.0, 2000.0],
            "sender_balance_before": [5000.0, 4000.0],
            "sender_balance_after": [4000.0, 2000.0],
            "type": ["TRANSFER", "CASH_OUT"],
            "device_id": ["d1", "d1"],
            "ip_address": ["i1", "i1"],
            "auth_method": ["SMS_OTP", "FACE_ID"],
            "isFraud": [0, 0],
        }
    )
    flipped = base.copy()
    flipped["isFraud"] = [1, 1]
    assert behavioural_risk_score(base) == behavioural_risk_score(flipped)


def test_seed_profiles_do_not_expose_the_label_as_a_scoring_field():
    from evaluation.benchmark_seed import build_seed_from_dataframe

    if not CSV.exists():
        pytest.skip("final.csv is not present")
    frame = pd.read_csv(CSV).sort_values("step", kind="stable")
    seed = build_seed_from_dataframe(frame.iloc[: int(len(frame) * 0.7)])
    for profile in seed["profiles"]:
        assert "fraud_ratio" not in profile, (
            "profiles must not carry a scoreable historical fraud rate; "
            "the agents read profile fields as evidence"
        )
        assert "behaviour_risk_score" in profile


# ------------------------------------------------------------- phase 1 rules


def test_retired_rules_would_match_nothing(df):
    """Both rules were written against demo data that no longer exists."""
    assert not df.ip_address.astype(str).str.lower().str.contains("vpn|tor|proxy").any()
    assert not df.device_id.astype(str).str.startswith("DEV_UNKNOWN").any()


def test_shared_infrastructure_lookups_are_registered():
    from infrastructure.databases.simulators import RedisSimulator

    sim = RedisSimulator()
    sim.register_infrastructure(
        [("A", "dev1", "ip1"), ("B", "dev1", "ip1"), ("C", "dev2", "ip2")]
    )
    assert sim.count_accounts_for_device("dev1") == 2
    assert sim.count_accounts_for_ip("ip1") == 2
    assert sim.count_accounts_for_device("dev2") == 1
    assert sim.is_known_device("A", "dev1")
    assert not sim.is_known_device("A", "dev2")
    # An account with no history must not be flagged just for having no past.
    assert sim.is_known_device("UNSEEN", "anything")
