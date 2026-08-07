"""The evidence layer has to work when the account has no history.

All nine of the original evidence signals are account aggregates, so on the
zero-shot holdout -- where 61% of senders had never been seen -- the five
databases returned nothing and the investigation scored F1 0.0000. These signals
read the transaction instead, which an account has even on its first ever
payment.
"""

from __future__ import annotations

import pytest

from core.evidence.transaction_signals import (
    SIGNAL_NAMES,
    PopulationStats,
    TypologyMatcher,
    transaction_signals,
)


def row(**overrides) -> dict:
    base = {
        "amount": 10_000.0,
        "sender_balance_before": 50_000.0,
        "sender_balance_after": 40_000.0,
        "oldbalanceDest": 25_000.0,
        "newbalanceDest": 35_000.0,
        "type": "TRANSFER",
        "auth_method": "SMART_OTP",
        "device_id": "known-device",
        "ip_address": "1.2.3.4",
    }
    base.update(overrides)
    return base


@pytest.fixture
def stats() -> PopulationStats:
    return PopulationStats.from_rows(
        [row(amount=a, device_id="known-device", ip_address="1.2.3.4") for a in (100, 1_000, 10_000, 100_000)]
    )


def test_every_signal_is_produced_for_an_unknown_account(stats):
    signals = transaction_signals(row(), stats)
    assert set(signals) == set(SIGNAL_NAMES)
    assert all(isinstance(v, float) for v in signals.values())


def test_signals_need_no_account_identity(stats):
    """The whole point: nothing here reads sender_account_no."""
    a = transaction_signals(row(), stats)
    b = transaction_signals({**row(), "sender_account_no": "NEVER_SEEN_BEFORE"}, stats)
    assert a == b


def test_full_drain_and_dormant_destination_are_detected(stats):
    signals = transaction_signals(
        row(sender_balance_after=0.0, oldbalanceDest=0.0, newbalanceDest=10_000.0), stats
    )
    assert signals["FULL_BALANCE_DRAIN"] == 1.0
    assert signals["DORMANT_DESTINATION"] == 1.0
    assert signals["DESTINATION_ABSORBS_ALL"] == 1.0


def test_weak_auth_counts_more_on_a_large_amount(stats):
    small = transaction_signals(row(amount=1_000.0, auth_method="SMS_OTP"), stats)
    large = transaction_signals(row(amount=900_000.0, auth_method="SMS_OTP"), stats)
    assert large["AUTH_WEAKER_THAN_POLICY"] > small["AUTH_WEAKER_THAN_POLICY"]
    strong = transaction_signals(row(amount=900_000.0, auth_method="FACE_ID"), stats)
    assert strong["AUTH_WEAKER_THAN_POLICY"] == 0.0


def test_unseen_device_and_ip_are_flagged(stats):
    signals = transaction_signals(row(device_id="brand-new", ip_address="9.9.9.9"), stats)
    assert signals["DEVICE_UNSEEN_GLOBALLY"] == 1.0
    assert signals["IP_UNSEEN_GLOBALLY"] == 1.0
    known = transaction_signals(row(), stats)
    assert known["DEVICE_UNSEEN_GLOBALLY"] == 0.0


def test_zero_balance_does_not_divide_by_zero(stats):
    signals = transaction_signals(row(sender_balance_before=0.0), stats)
    assert signals["DRAIN_RATIO"] == 0.0


def test_typology_matcher_separates_the_shapes_it_was_fitted_on(stats):
    fraud_like = [
        transaction_signals(
            row(sender_balance_after=0.0, oldbalanceDest=0.0, auth_method="SMS_OTP", device_id="new"),
            stats,
        )
        for _ in range(8)
    ]
    legit_like = [transaction_signals(row(), stats) for _ in range(8)]
    matcher = TypologyMatcher().fit(fraud_like + legit_like, [1] * 8 + [0] * 8)
    assert matcher.similarity(fraud_like[0]) > matcher.similarity(legit_like[0])


def test_typology_matcher_is_safe_before_fitting(stats):
    assert TypologyMatcher().similarity(transaction_signals(row(), stats)) == 0.0


def test_typology_matcher_ignores_a_single_class(stats):
    signals = [transaction_signals(row(), stats)]
    assert TypologyMatcher().fit(signals, [1]).fraud_centroid == {}


def test_population_stats_handle_an_empty_corpus():
    empty = PopulationStats.from_rows([])
    assert empty.amount_percentile(1_000.0) == 0.5
