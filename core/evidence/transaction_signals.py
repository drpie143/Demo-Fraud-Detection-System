"""Evidence that does not need the account to have a history.

The nine signals the executor collects are all account aggregates: profile risk,
history volume, drain history, distinct recipients, graph neighbours, shared
infrastructure, velocity. They score F1 0.4628 when every fraud mechanism is
labelled and **0.0000** on the zero-shot holdout, because 61% of those senders
have never been seen before. There is nothing to look up, so the five databases
return nothing and the investigation has no material.

That is the wrong place to give up: an account with no history is exactly when a
human analyst falls back on the transaction itself and on what known fraud looks
like. This module provides both.

`transaction_signals` reads only the transaction and population statistics, so
it works on the first transaction an account ever makes.

`TypologyMatcher` learns prototypes from whatever fraud *is* labelled and scores
similarity to them. When CASH_OUT fraud is unlabelled, the prototypes come from
TRANSFER fraud, and the question of whether they transfer is precisely the
generalisation an investigation layer is supposed to provide.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# Bank policy: larger transfers should carry stronger authentication. A weak
# channel on a large amount is a mismatch worth noting, independent of history.
LARGE_AMOUNT = 500_000
MEDIUM_AMOUNT = 50_000
WEAK_AUTH = "SMS_OTP"

SIGNAL_NAMES = [
    "TXN_AMOUNT_PERCENTILE",
    "FULL_BALANCE_DRAIN",
    "DRAIN_RATIO",
    "DORMANT_DESTINATION",
    "DESTINATION_ABSORBS_ALL",
    "AUTH_WEAKER_THAN_POLICY",
    "DEVICE_UNSEEN_GLOBALLY",
    "IP_UNSEEN_GLOBALLY",
    "HIGH_RISK_CHANNEL",
]


@dataclass
class PopulationStats:
    """Population-level context, built from the train split only."""

    amount_quantiles: list[float] = field(default_factory=list)
    known_devices: set[str] = field(default_factory=set)
    known_ips: set[str] = field(default_factory=set)

    @classmethod
    def from_rows(cls, rows: list[dict[str, Any]]) -> PopulationStats:
        amounts = sorted(float(r.get("amount") or 0.0) for r in rows)
        quantiles = (
            [amounts[min(len(amounts) - 1, int(len(amounts) * q / 100))] for q in range(101)]
            if amounts
            else []
        )
        return cls(
            amount_quantiles=quantiles,
            known_devices={str(r.get("device_id")) for r in rows if r.get("device_id")},
            known_ips={str(r.get("ip_address")) for r in rows if r.get("ip_address")},
        )

    def amount_percentile(self, amount: float) -> float:
        if not self.amount_quantiles:
            return 0.5
        lo, hi = 0, len(self.amount_quantiles) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if self.amount_quantiles[mid] < amount:
                lo = mid + 1
            else:
                hi = mid
        return lo / max(1, len(self.amount_quantiles) - 1)


def transaction_signals(row: dict[str, Any], stats: PopulationStats) -> dict[str, float]:
    """Signals available on an account's very first transaction."""
    amount = float(row.get("amount") or 0.0)
    before = float(row.get("sender_balance_before") or 0.0)
    after = float(row.get("sender_balance_after") or 0.0)
    old_dest = float(row.get("oldbalanceDest") or 0.0)
    new_dest = float(row.get("newbalanceDest") or 0.0)
    txn_type = str(row.get("type") or "")
    auth = str(row.get("auth_method") or "")

    if amount >= LARGE_AMOUNT:
        expected_weak = 0.12
    elif amount >= MEDIUM_AMOUNT:
        expected_weak = 0.28
    else:
        expected_weak = 0.50

    return {
        "TXN_AMOUNT_PERCENTILE": stats.amount_percentile(amount),
        "FULL_BALANCE_DRAIN": 1.0 if (before > 0 and after <= 0) else 0.0,
        "DRAIN_RATIO": min(amount / before, 2.0) if before > 0 else 0.0,
        "DORMANT_DESTINATION": 1.0 if old_dest <= 0 else 0.0,
        "DESTINATION_ABSORBS_ALL": 1.0 if abs(new_dest - old_dest - amount) < 0.005 else 0.0,
        # Positive when the channel is weaker than the amount would justify.
        "AUTH_WEAKER_THAN_POLICY": (1.0 - expected_weak) if auth == WEAK_AUTH else 0.0,
        "DEVICE_UNSEEN_GLOBALLY": 0.0 if str(row.get("device_id")) in stats.known_devices else 1.0,
        "IP_UNSEEN_GLOBALLY": 0.0 if str(row.get("ip_address")) in stats.known_ips else 1.0,
        "HIGH_RISK_CHANNEL": 1.0 if txn_type in {"TRANSFER", "CASH_OUT"} else 0.0,
    }


class TypologyMatcher:
    """Similarity to fraud patterns learnt from the labelled mechanisms.

    This is the deterministic stand-in for what the vector store is meant to do:
    hold descriptions of known fraud shapes and let an investigation compare an
    unfamiliar transaction against them. It carries no account identity, so it
    works on an account seen for the first time, and it is fitted only on fraud
    that was labelled -- if CASH_OUT is masked, the prototype is built from
    TRANSFER fraud and has to generalise to reach it.
    """

    def __init__(self) -> None:
        self.fraud_centroid: dict[str, float] = {}
        self.legit_centroid: dict[str, float] = {}
        self.scale: dict[str, float] = {}

    def fit(self, signals: list[dict[str, float]], labels: list[int]) -> TypologyMatcher:
        fraud = [s for s, y in zip(signals, labels, strict=True) if y]
        legit = [s for s, y in zip(signals, labels, strict=True) if not y]
        if not fraud or not legit:
            return self
        for name in SIGNAL_NAMES:
            values = [s[name] for s in signals]
            mean = sum(values) / len(values)
            variance = sum((v - mean) ** 2 for v in values) / len(values)
            self.scale[name] = math.sqrt(variance) or 1.0
            self.fraud_centroid[name] = sum(s[name] for s in fraud) / len(fraud)
            self.legit_centroid[name] = sum(s[name] for s in legit) / len(legit)
        return self

    def _distance(self, signals: dict[str, float], centroid: dict[str, float]) -> float:
        return math.sqrt(
            sum(
                ((signals[name] - centroid[name]) / self.scale[name]) ** 2
                for name in SIGNAL_NAMES
            )
        )

    def similarity(self, signals: dict[str, float]) -> float:
        """0 means it looks legitimate, 1 means it matches a known fraud shape."""
        if not self.fraud_centroid:
            return 0.0
        to_fraud = self._distance(signals, self.fraud_centroid)
        to_legit = self._distance(signals, self.legit_centroid)
        total = to_fraud + to_legit
        return 0.5 if total == 0 else to_legit / total
