"""Score the investigation evidence without calling an LLM.

The Phase 2 agents do two things: decide which stores to query, and reason over
what comes back. The second is what Gemini is for. The first is deterministic --
there are nine evidence signals and nothing stops us collecting all of them.

Collecting all nine and scoring them directly answers a question the agent
benchmark cannot: how much of the pipeline's result comes from querying five
databases, and how much comes from the reasoning on top. The gap between this
and the Gemini run is the LLM's actual contribution, separated from the
retrieval work.

It also gives a ceiling. A logistic regression fitted on the same evidence is
close to the best any linear decision layer can do with it, so if that lands at
0.65 then no amount of reasoning over the same evidence reaches 0.80 -- the
information is not there. That bound costs no quota and runs in seconds.

What this does not do is predict what Gemini decides. It measures evidence, not
reasoning.

Usage:
    python evaluation/evaluate_evidence_only.py
    python evaluation/evaluate_evidence_only.py --held-out CASH_OUT
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.evidence.transaction_signals import (
    SIGNAL_NAMES,
    PopulationStats,
    TypologyMatcher,
    transaction_signals,
)
from evaluation.baseline_models import bootstrap_ci, metrics
from evaluation.benchmark_seed import behavioural_risk_score

LABEL = "isFraud"

# The nine signals core/agents/executor.py emits, in the order it emits them.
EVIDENCE_COLUMNS = [
    "PROFILE_RISK",
    "HISTORICAL_BEHAVIOUR_RISK",
    "NEW_ACCOUNT",
    "HIGH_HISTORY_VOLUME",
    "BALANCE_DRAIN_HISTORY",
    "MULTI_RECIPIENT_PATTERN",
    "RISKY_GRAPH_NEIGHBORS",
    "SHARED_INFRASTRUCTURE",
    "HIGH_VELOCITY",
]


def collect_evidence(train: pd.DataFrame, target: pd.DataFrame) -> pd.DataFrame:
    """Reproduce the executor's evidence set from train-split state only."""
    behaviour = {
        account: behavioural_risk_score(group)
        for account, group in train.groupby("sender_account_no")
    }
    history_count = train.groupby("sender_account_no").size().to_dict()
    drained = (
        train.assign(_d=(pd.to_numeric(train.sender_balance_after, errors="coerce") <= 500))
        .groupby("sender_account_no")["_d"]
        .sum()
        .to_dict()
    )
    receivers = train.groupby("sender_account_no")["receiver_account_no"].nunique().to_dict()

    device_accounts: dict[str, set] = {}
    ip_accounts: dict[str, set] = {}
    for row in train.itertuples():
        device_accounts.setdefault(row.device_id, set()).add(row.sender_account_no)
        ip_accounts.setdefault(row.ip_address, set()).add(row.sender_account_no)

    # A neighbour is risky when its own behaviour score is high -- never because
    # of its label, which is the leak this project already had to remove once.
    risky_accounts = {a for a, s in behaviour.items() if s >= 0.48}
    counterparties: dict[str, set] = {}
    for row in train.itertuples():
        counterparties.setdefault(row.sender_account_no, set()).add(row.receiver_account_no)
        counterparties.setdefault(row.receiver_account_no, set()).add(row.sender_account_no)

    out = pd.DataFrame(index=target.index)
    accounts = target["sender_account_no"]
    out["PROFILE_RISK"] = [behaviour.get(a, 0.1) for a in accounts]
    out["HISTORICAL_BEHAVIOUR_RISK"] = out["PROFILE_RISK"]
    out["NEW_ACCOUNT"] = [1 if a not in history_count else 0 for a in accounts]
    out["HIGH_HISTORY_VOLUME"] = [history_count.get(a, 0) for a in accounts]
    out["BALANCE_DRAIN_HISTORY"] = [float(drained.get(a, 0)) for a in accounts]
    out["MULTI_RECIPIENT_PATTERN"] = [receivers.get(a, 0) for a in accounts]
    out["RISKY_GRAPH_NEIGHBORS"] = [
        len(counterparties.get(a, set()) & risky_accounts) for a in accounts
    ]
    out["SHARED_INFRASTRUCTURE"] = [
        max(
            len(device_accounts.get(d, ())),
            len(ip_accounts.get(i, ())),
        )
        for d, i in zip(target["device_id"], target["ip_address"], strict=True)
    ]
    out["HIGH_VELOCITY"] = [history_count.get(a, 0) for a in accounts]
    return out


def deterministic_score(evidence: pd.DataFrame) -> list[int]:
    """A transparent scorer over the same evidence, in the pipeline's idiom."""
    predictions = []
    for row in evidence.itertuples():
        score = 0.0
        if row.PROFILE_RISK >= 0.62:
            score += 0.35
        elif row.PROFILE_RISK >= 0.48:
            score += 0.20
        if row.NEW_ACCOUNT:
            score += 0.10
        if row.BALANCE_DRAIN_HISTORY >= 2:
            score += 0.20
        if row.MULTI_RECIPIENT_PATTERN >= 3:
            score += 0.15
        if row.RISKY_GRAPH_NEIGHBORS >= 1:
            score += 0.20
        if row.SHARED_INFRASTRUCTURE > 3:
            score += 0.20
        if row.HIGH_VELOCITY > 5:
            score += 0.15
        predictions.append(1 if score >= 0.5 else 0)
    return predictions


def report(name: str, y_true: list[int], y_pred: list[int], rounds: int, seed: int) -> dict:
    m = metrics(y_true, y_pred)
    lo, hi = bootstrap_ci(y_true, y_pred, "f1", rounds, seed)
    print(
        f"  {name:<40} P={m['precision']:.4f} R={m['recall']:.4f} "
        f"F1={m['f1']:.4f}  95% CI [{lo:.4f}, {hi:.4f}]"
    )
    return {**m, "f1_ci95": [round(lo, 4), round(hi, 4)]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Score investigation evidence without an LLM.")
    parser.add_argument("--csv", default="final.csv")
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument("--held-out", default="CASH_OUT")
    parser.add_argument("--bootstrap", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="evaluation/results/evidence_only.json")
    args = parser.parse_args()

    df = pd.read_csv(args.csv).sort_values("step", kind="stable").reset_index(drop=True)
    cut = int(len(df) * args.train_frac)
    train, holdout = df.iloc[:cut].copy(), df.iloc[cut:].copy()
    results: dict = {}

    def enriched_evidence(tr: pd.DataFrame, te: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Account-history evidence plus signals that survive an unknown account."""
        stats = PopulationStats.from_rows(tr.to_dict("records"))
        tr_signals = [transaction_signals(r, stats) for r in tr.to_dict("records")]
        te_signals = [transaction_signals(r, stats) for r in te.to_dict("records")]

        matcher = TypologyMatcher().fit(tr_signals, [int(v) for v in tr[LABEL]])

        def frame(base: pd.DataFrame, signals: list[dict[str, float]]) -> pd.DataFrame:
            out = base.copy()
            for name in SIGNAL_NAMES:
                out[name] = [s[name] for s in signals]
            out["TYPOLOGY_MATCH"] = [matcher.similarity(s) for s in signals]
            return out

        return (
            frame(collect_evidence(tr, tr), tr_signals),
            frame(collect_evidence(tr, te), te_signals),
        )

    def fit_and_report(
        name: str, ev_tr: pd.DataFrame, ev_te: pd.DataFrame, y_tr: list[int], y_te: list[int]
    ) -> dict | None:
        if sum(y_tr) == 0:
            return None
        scaler = StandardScaler().fit(ev_tr)
        model = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=args.seed)
        model.fit(scaler.transform(ev_tr), y_tr)
        pred = [int(v) for v in model.predict(scaler.transform(ev_te))]
        return report(name, y_te, pred, args.bootstrap, args.seed)

    def run_block(title: str, tr: pd.DataFrame, te: pd.DataFrame) -> dict:
        print(f"\n--- {title}  (n={len(te)}, fraud={int(te[LABEL].sum())}) ---")
        y_tr = [int(v) for v in tr[LABEL]]
        y_te = [int(v) for v in te[LABEL]]
        ev_tr, ev_te = collect_evidence(tr, tr), collect_evidence(tr, te)

        block = {"n": len(te), "fraud": sum(y_te)}
        block["account_history_rule_scorer"] = report(
            "account history, rule scorer", y_te, deterministic_score(ev_te), args.bootstrap, args.seed
        )
        block["account_history_ceiling"] = fit_and_report(
            "account history, fitted ceiling", ev_tr, ev_te, y_tr, y_te
        )

        rich_tr, rich_te = enriched_evidence(tr, te)
        block["with_transaction_signals"] = fit_and_report(
            "+ transaction-level signals", rich_tr, rich_te, y_tr, y_te
        )

        # Typology matching on its own, with no account identity at all.
        typ_tr = rich_tr[[*SIGNAL_NAMES, "TYPOLOGY_MATCH"]]
        typ_te = rich_te[[*SIGNAL_NAMES, "TYPOLOGY_MATCH"]]
        block["transaction_and_typology_only"] = fit_and_report(
            "transaction + typology only (no history)", typ_tr, typ_te, y_tr, y_te
        )
        return block

    results["all_labelled"] = run_block("every mechanism labelled", train, holdout)

    mechanism = args.held_out
    masked = train.copy()
    masked.loc[(masked.type == mechanism) & (masked[LABEL] == 1), LABEL] = 0
    target = holdout[holdout.type == mechanism]
    if len(target) and target[LABEL].sum():
        results["zero_shot"] = run_block(
            f"'{mechanism}' fraud unlabelled in training", masked, target
        )

    print(
        "\nThese numbers bound the agent, they do not stand in for it. Anything the\n"
        "Gemini run scores above the fitted line is reasoning the evidence did not\n"
        "already contain; anything below means the LLM is losing information the\n"
        "queries had already found."
    )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nSaved -> {out}")


if __name__ == "__main__":
    main()
