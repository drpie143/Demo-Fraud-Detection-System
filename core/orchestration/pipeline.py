# ====================================================================
# ORCHESTRATOR.PY - LangGraph Pipeline Orchestration
# ====================================================================
#
# THAY ĐỔI:
#   Cũ: Class-based orchestrator với if/else routing thủ công
#   Mới: LangGraph StateGraph với conditional edges
#
# GRAPH FLOW:
#   START
#     ↓
#   [phase1_screening] → Enriched Phase1Result (RuleDetail, AccountFlags)
#     ↓
#   {route_after_phase1}
#     ├── GREEN → END (allow)
#     ├── RED   → END (block)
#     └── YELLOW → [planner]
#                     ↓
#                  [executor] → query Neo4j + ChromaDB + MongoDB Atlas
#                     ↓
#                  [vision]  → Gemini đọc/phân tích kết quả executor
#                     ↓
#                  [planner_evaluate] → Planner đánh giá vision analysis
#                     ↓
#                  {route_after_evaluate}
#                     ├── done → [report_generator] → [detective] → END
#                     └── not_done → [executor] (loop)
#
# LangGraph:
#   - Mỗi node là 1 function nhận state → trả state mới
#   - Conditional edges: routing dựa trên state values
#   - Built-in state management: không cần global variables
# ====================================================================

from __future__ import annotations

import logging
import uuid
import threading
from datetime import datetime
from typing import TypedDict, Optional, Annotated

from langgraph.graph import StateGraph, END

from core.schemas.models import (
    Transaction, Phase1Result, RuleDetail, AccountFlags,
    InvestigationRequest, PlannerTask, ExecutorResult,
    InvestigationReport, DecisionResult,
    RiskLevel, FinalDecision, TaskType,
)
from core.evidence.transaction_signals import (
    PopulationStats,
    TypologyMatcher,
    transaction_signals,
)
from infrastructure.databases.simulators import redis_service, redis_sim, dynamodb_sim, neptune_sim
from infrastructure.databases.seed_loader import apply_processed_seed_to_simulators
from infrastructure.databases.mongodb import mongodb_client
from infrastructure.databases.neo4j import neo4j_client
from infrastructure.databases.chroma import vector_store
from core.agents.planner import PlannerAgent

logger = logging.getLogger(__name__)
from core.agents.executor import ExecutorAgent
from core.agents.report import ReportAgent
from core.agents.detective import DetectiveAgent
from core.agents.vision import vision_agent
from configs.settings import settings


# =====================================================================
# LANGGRAPH STATE - TypedDict for state management
# =====================================================================

class GraphState(TypedDict, total=False):
    """
    State object cho LangGraph pipeline.
    
    Mỗi node đọc fields cần thiết và cập nhật fields output.
    LangGraph tự merge state updates.
    """
    # Input
    transaction: Optional[dict]  # Transaction as dict
    
    # Phase 1
    phase1_result: Optional[dict]  # Phase1Result as dict
    phase1_risk_level: str  # "green" / "yellow" / "red"
    
    # Phase 2 - Planning
    investigation_request: Optional[dict]
    current_tasks: list  # list[PlannerTask dicts]
    all_results: list  # list[ExecutorResult dicts]
    investigation_step: int
    investigation_done: bool
    planner_confidence: float
    
    # Phase 2 - Vision Analysis
    vision_analysis: Optional[dict]  # VisionAgent output
    
    # Phase 2 - Report
    report: Optional[dict]
    
    # Phase 3
    decision: Optional[dict]
    
    # Final output
    final_decision: str  # "allow" / "block" / "escalate"
    final_message: str
    
    # Meta
    error: Optional[str]


def make_initial_state(transaction: Transaction) -> GraphState:
    return {
        "transaction": transaction.model_dump(),
        "phase1_result": None,
        "phase1_risk_level": "",
        "investigation_request": None,
        "current_tasks": [],
        "all_results": [],
        "investigation_step": 0,
        "investigation_done": False,
        "planner_confidence": 0.0,
        "vision_analysis": None,
        "report": None,
        "decision": None,
        "final_decision": "",
        "final_message": "",
        "error": None,
    }


# =====================================================================
# NODE: PHASE 1 SCREENING (Enriched)
# =====================================================================

def phase1_screening(state: GraphState) -> GraphState:
    """
    Phase 1: Real-time Screening với Redis Simulator.
    
    ENRICHED: Tạo Phase1Result chi tiết gồm:
    - triggered_rules → list[RuleDetail] (severity, value, threshold)
    - sender_flags, receiver_flags → AccountFlags
    - context_summary → text cho Planner
    
    Routing logic:
    - Sender whitelisted + amount <= configured instant_allow_max + score < 0.2 → GREEN
    - Sender blacklisted OR score > 0.9 → RED
    - Còn lại → YELLOW (cần điều tra)
    """
    txn_dict = state["transaction"]
    txn = Transaction(**txn_dict)
    
    print(f"\n{'#'*70}")
    print(f"# PHASE 1: Real-Time Screening")
    print(f"# Transaction: {txn.transaction_id}")
    print(f"# {txn.sender_id} -> {txn.receiver_id}: {txn.amount:,.2f} {txn.currency}")
    print(f"{'#'*70}")
    
    triggered_rules: list[RuleDetail] = []
    risk_score = 0.0
    
    # ─── Increment velocity counter (ref: fraud-detection/phase1.py Check 4) ───
    redis_service.increment_velocity(txn.sender_id)
    
    # ─── Rule 1: Blacklist check ───
    if redis_service.is_blacklisted(txn.sender_id):
        triggered_rules.append(RuleDetail(
            rule="SENDER_BLACKLISTED",
            severity="critical",
            detail=f"Sender {txn.sender_id} is on blacklist",
        ))
        risk_score += 0.5
    
    if redis_service.is_blacklisted(txn.receiver_id):
        triggered_rules.append(RuleDetail(
            rule="RECEIVER_BLACKLISTED",
            severity="critical",
            detail=f"Receiver {txn.receiver_id} is on blacklist",
        ))
        risk_score += 0.3
    
    # ─── Rule 2: Risk score hiện tại ───
    sender_risk = redis_service.get_risk_score(txn.sender_id)
    if sender_risk > settings.high_risk_threshold:
        triggered_rules.append(RuleDetail(
            rule="HIGH_RISK_SCORE",
            severity="high",
            value=sender_risk,
            threshold=settings.high_risk_threshold,
            detail=f"Sender risk score {sender_risk:.2f} > threshold {settings.high_risk_threshold:.2f}",
        ))
        risk_score += sender_risk * 0.3
    
    # ─── Rule 3: High velocity ───
    velocity_1h = redis_service.get_velocity(txn.sender_id, hours=1)
    if velocity_1h > settings.suspicious_velocity_threshold:
        triggered_rules.append(RuleDetail(
            rule="HIGH_VELOCITY",
            severity="high",
            value=float(velocity_1h),
            threshold=float(settings.suspicious_velocity_threshold),
            detail=f"{velocity_1h} GD trong 1 giờ qua (threshold: {settings.suspicious_velocity_threshold})",
        ))
        risk_score += 0.15
    
    # ─── Rule 4: Large amount ───
    if txn.amount > settings.large_amount_threshold:
        triggered_rules.append(RuleDetail(
            rule="LARGE_AMOUNT",
            severity="high",
            value=txn.amount,
            threshold=settings.large_amount_threshold,
            detail=f"Số tiền {txn.amount:,.2f} {txn.currency} > threshold {settings.large_amount_threshold:,.2f} {txn.currency}",
        ))
        risk_score += 0.15
    elif txn.amount > settings.elevated_amount_threshold:
        triggered_rules.append(RuleDetail(
            rule="ELEVATED_AMOUNT",
            severity="medium",
            value=txn.amount,
            threshold=settings.elevated_amount_threshold,
            detail=f"Số tiền {txn.amount:,.2f} {txn.currency} > threshold {settings.elevated_amount_threshold:,.2f} {txn.currency}",
        ))
        risk_score += 0.08
    
    # Dataset rule: balance drain is a strong PaySim fraud signal.
    if txn.sender_balance_before and txn.sender_balance_before > 0:
        remaining_ratio = (
            txn.sender_balance_after / txn.sender_balance_before
            if txn.sender_balance_after is not None
            else 1.0
        )
        if txn.sender_balance_after is not None and (
            txn.sender_balance_after <= 500 or remaining_ratio <= 0.1
        ):
            triggered_rules.append(RuleDetail(
                rule="BALANCE_DRAIN",
                severity="high",
                value=txn.sender_balance_after,
                threshold=500.0,
                detail=(
                    f"Sender balance drained from {txn.sender_balance_before:,.2f} "
                    f"to {txn.sender_balance_after:,.2f}"
                ),
            ))
            risk_score += 0.2

    # ─── Rule 5: Structuring pattern ───
    if any(0 < band - txn.amount <= max(band * 0.02, 100) for band in (5000, 10000, 20000, 50000)):
        triggered_rules.append(RuleDetail(
            rule="STRUCTURING_SUSPICION",
            severity="high",
            value=txn.amount,
            threshold=0.0,
            detail=f"Số tiền {txn.amount:,.2f} {txn.currency} ngay dưới reporting threshold",
        ))
        risk_score += 0.2
    
    # ─── Rule 6: IP shared across many accounts ───
    # Previously this matched IPs containing "vpn"/"tor"/"proxy". Dataset
    # addresses are plain dotted quads, so the rule fired on zero of 1,043 rows.
    # Shared network infrastructure is the signal that actually exists here:
    # an address used by more than a handful of accounts is 2.9x base rate.
    ip_accounts = redis_service.count_accounts_for_ip(txn.ip_address) if txn.ip_address else 0
    if ip_accounts > settings.shared_ip_account_threshold:
        triggered_rules.append(RuleDetail(
            rule="SHARED_IP_INFRASTRUCTURE",
            severity="high",
            value=float(ip_accounts),
            threshold=float(settings.shared_ip_account_threshold),
            detail=f"IP {txn.ip_address} dùng chung bởi {ip_accounts} tài khoản",
        ))
        risk_score += 0.15

    # ─── Rule 7: Device not previously seen for this account ───
    # Previously this matched device ids starting with "DEV_UNKNOWN", a prefix
    # from retired demo data that appears on zero rows of the real dataset.
    if txn.device_id and not redis_service.is_known_device(txn.sender_id, txn.device_id):
        triggered_rules.append(RuleDetail(
            rule="UNKNOWN_DEVICE",
            severity="medium",
            detail=f"Device {txn.device_id} chưa từng thấy ở tài khoản này",
        ))
        risk_score += 0.1

    # ─── Rule 8: Device shared across many accounts ───
    device_accounts = (
        redis_service.count_accounts_for_device(txn.device_id) if txn.device_id else 0
    )
    if device_accounts > settings.shared_device_account_threshold:
        triggered_rules.append(RuleDetail(
            rule="SHARED_DEVICE_RING",
            severity="high",
            value=float(device_accounts),
            threshold=float(settings.shared_device_account_threshold),
            detail=f"Thiết bị {txn.device_id} dùng chung bởi {device_accounts} tài khoản",
        ))
        risk_score += 0.15


    # ─── Clamp risk score ───
    risk_score = min(risk_score, 1.0)
    
    # ─── Account flags ───
    sender_flags = AccountFlags(
        account_id=txn.sender_id,
        is_whitelisted=redis_service.is_whitelisted(txn.sender_id),
        is_blacklisted=redis_service.is_blacklisted(txn.sender_id),
        risk_score=redis_service.get_risk_score(txn.sender_id),
        velocity_1h=redis_service.get_velocity(txn.sender_id, hours=1),
        velocity_24h=redis_service.get_velocity(txn.sender_id, hours=24),
    )
    
    receiver_flags = AccountFlags(
        account_id=txn.receiver_id,
        is_whitelisted=redis_service.is_whitelisted(txn.receiver_id),
        is_blacklisted=redis_service.is_blacklisted(txn.receiver_id),
        risk_score=redis_service.get_risk_score(txn.receiver_id),
        velocity_1h=redis_service.get_velocity(txn.receiver_id, hours=1),
        velocity_24h=redis_service.get_velocity(txn.receiver_id, hours=24),
    )
    
    # ─── Routing decision ───
    if (
        sender_flags.is_whitelisted
        and not any(r.severity == "critical" for r in triggered_rules)
        and txn.amount <= settings.instant_allow_max
        and risk_score < 0.2
    ):
        risk_level = RiskLevel.GREEN
    elif (
        sender_flags.is_blacklisted
        or receiver_flags.is_blacklisted
        or any(r.severity == "critical" for r in triggered_rules)
        or sender_flags.risk_score >= settings.red_risk_threshold
        or risk_score > 0.95
    ):
        risk_level = RiskLevel.RED
    else:
        risk_level = RiskLevel.YELLOW
    
    # ─── Context summary cho Planner ───
    context_parts = [f"Giao dịch {txn.amount:,.2f} {txn.currency} từ {txn.sender_id} đến {txn.receiver_id}."]
    if triggered_rules:
        context_parts.append(f"Triggered {len(triggered_rules)} rules: " +
                             ", ".join(r.rule for r in triggered_rules) + ".")
    if sender_flags.is_blacklisted:
        context_parts.append(f"Sender {txn.sender_id} is BLACKLISTED.")
    if receiver_flags.is_blacklisted:
        context_parts.append(f"Receiver {txn.receiver_id} is BLACKLISTED.")
    if sender_flags.velocity_1h > 5:
        context_parts.append(f"Sender velocity bất thường: {sender_flags.velocity_1h} GD/1h.")
    context_summary = " ".join(context_parts)
    
    phase1 = Phase1Result(
        transaction_id=txn.transaction_id,
        risk_level=risk_level,
        risk_score=risk_score,
        triggered_rules=triggered_rules,
        sender_flags=sender_flags,
        receiver_flags=receiver_flags,
        context_summary=context_summary,
        requires_investigation=(risk_level == RiskLevel.YELLOW),
        message=f"Phase 1: {risk_level.value.upper()} (score={risk_score:.3f}, rules={len(triggered_rules)})",
    )
    
    # ─── Log ───
    color = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    print(f"\n   Result: {color.get(risk_level.value, '?')} {risk_level.value.upper()}")
    print(f"   Risk Score: {risk_score:.3f}")
    print(f"   Rules: {len(triggered_rules)}")
    for r in triggered_rules:
        print(f"     [{r.severity.upper()}] {r.rule}: {r.detail}")
    print(f"   Sender: wl={sender_flags.is_whitelisted}, bl={sender_flags.is_blacklisted}, "
          f"risk={sender_flags.risk_score:.2f}, vel_1h={sender_flags.velocity_1h}")
    print(f"   Receiver: wl={receiver_flags.is_whitelisted}, bl={receiver_flags.is_blacklisted}, "
          f"risk={receiver_flags.risk_score:.2f}")
    
    return {
        "phase1_result": phase1.model_dump(),
        "phase1_risk_level": risk_level.value,
    }


# =====================================================================
# ROUTING: After Phase 1
# =====================================================================

def route_after_phase1(state: GraphState) -> str:
    """
    Conditional routing sau Phase 1.
    
    GREEN → "end_allow" (pass through, allow)
    RED   → "end_block" (block immediately)
    YELLOW → "planner" (cần điều tra)
    """
    risk_level = state.get("phase1_risk_level", "yellow")
    
    if risk_level == "green":
        print(f"\n   🟢 ROUTING: GREEN → ALLOW (skip investigation)")
        return "end_allow"
    elif risk_level == "red":
        print(f"\n   🔴 ROUTING: RED → BLOCK (auto-reject)")
        return "end_block"
    else:
        print(f"\n   🟡 ROUTING: YELLOW → Investigation pipeline")
        return "planner"


# =====================================================================
# NODE: END_ALLOW (Green path - skip investigation)
# =====================================================================

def end_allow(state: GraphState) -> GraphState:
    """Kết thúc GREEN: cho phép giao dịch."""
    txn = state.get("transaction", {})
    phase1 = state.get("phase1_result", {})
    
    print(f"\n   ✅ ALLOWED: {txn.get('transaction_id', '?')}")
    print(f"   Risk score: {phase1.get('risk_score', 0):.3f}")
    
    return {
        "final_decision": "allow",
        "final_message": (
            f"Transaction {txn.get('transaction_id', '?')} ALLOWED. "
            f"Sender whitelisted, low risk ({phase1.get('risk_score', 0):.3f})."
        ),
        "decision": {
            "decision": "allow",
            "confidence": 0.95,
            "reasoning": "Phase 1 GREEN: sender whitelisted, low risk, small amount",
        },
    }


# =====================================================================
# NODE: END_BLOCK (Red path - auto-block)
# =====================================================================

def end_block(state: GraphState) -> GraphState:
    """Kết thúc RED: chặn giao dịch ngay."""
    txn = state.get("transaction", {})
    phase1 = state.get("phase1_result", {})
    sender_id = txn.get("sender_id", "")
    
    print(f"\n   🚫 BLOCKED: {txn.get('transaction_id', '?')}")
    print(f"   Risk score: {phase1.get('risk_score', 0):.3f}")
    
    # Phase 3 enforcement (auto-block)
    if sender_id:
        redis_service.update_blacklist(sender_id, add=True)
        redis_service.update_risk_score(sender_id, 0.95)
        print(f"   → Blacklisted: {sender_id}")
    
    return {
        "final_decision": "block",
        "final_message": (
            f"Transaction {txn.get('transaction_id', '?')} BLOCKED. "
            f"Phase 1 RED: risk={phase1.get('risk_score', 0):.3f}."
        ),
        "decision": {
            "decision": "block",
            "confidence": 0.99,
            "reasoning": "Phase 1 RED: blacklisted sender or extreme risk score",
        },
    }


# =====================================================================
# NODE: PLANNER (LLM-driven investigation planning)
# =====================================================================

# Shared agent instances (reset per investigation)
# IMPORTANT: _processing_lock ensures only ONE investigation runs at a time.
# This prevents state leakage between concurrent requests.
# Each investigation calls reset() before starting.
_planner = PlannerAgent()
_executor = ExecutorAgent()
_report_agent = ReportAgent()
_detective = DetectiveAgent()
_processing_lock = threading.Lock()


def planner_node(state: GraphState) -> GraphState:
    """
    Planner Agent: tạo investigation plan bằng Gemini LLM.
    
    Nhận Phase1Result enriched → LLM phân tích → tạo tasks.
    """
    _planner.reset()
    
    txn = Transaction(**state["transaction"])
    phase1 = Phase1Result(**state["phase1_result"])
    
    request = InvestigationRequest(
        request_id=f"REQ_{uuid.uuid4().hex[:8]}",
        transaction=txn,
        phase1_result=phase1,
        priority=8 if phase1.risk_score > 0.5 else 5,
    )
    
    tasks = _planner.create_investigation_plan(request)
    
    return {
        "investigation_request": request.model_dump(),
        "current_tasks": [t.model_dump() for t in tasks],
        "all_results": [],
        "investigation_step": 0,
        "investigation_done": False,
        "planner_confidence": 0.0,
    }


# =====================================================================
# NODE: EXECUTOR (Execute tasks, collect evidence)
# =====================================================================

def executor_node(state: GraphState) -> GraphState:
    """
    Executor Agent: thực thi batch tasks, thu thập evidence.
    """
    task_dicts = state.get("current_tasks", [])
    tasks = [PlannerTask(**t) for t in task_dicts]

    results = _executor.execute_batch(tasks)

    existing_results = state.get("all_results", [])
    all_results = existing_results + [r.model_dump() for r in results]

    # Evidence the account does not need a history for. Every tool above queries
    # account aggregates, so on a first-seen sender they all return nothing --
    # measured at F1 0.0000 on the zero-shot holdout, where 61% of senders had
    # never been observed. These signals read the transaction itself.
    transaction_evidence = _transaction_evidence(state.get("transaction", {}))
    if transaction_evidence is not None:
        all_results = all_results + [transaction_evidence.model_dump()]

    return {
        "all_results": all_results,
        "investigation_step": state.get("investigation_step", 0) + 1,
    }


# Population context and fraud typologies are built once, from the train split
# only. Reading the whole file here would let a holdout transaction contribute to
# the statistics it is scored against.
_TRAIN_FRAC = 0.7
_population_cache: dict[str, object] = {}


def _train_rows() -> list[dict]:
    from infrastructure.databases.seed_loader import load_final_csv_rows

    rows = load_final_csv_rows()
    rows = sorted(rows, key=lambda r: int(float(r.get("step") or 0)))
    return rows[: int(len(rows) * _TRAIN_FRAC)]


def _population_stats() -> PopulationStats:
    if "stats" not in _population_cache:
        _population_cache["stats"] = PopulationStats.from_rows(_train_rows())
    return _population_cache["stats"]  # type: ignore[return-value]


def _typology_matcher() -> TypologyMatcher | None:
    if "matcher" not in _population_cache:
        try:
            rows = _train_rows()
            stats = _population_stats()
            signals = [transaction_signals(r, stats) for r in rows]
            labels = [int(float(r.get("isFraud") or 0)) for r in rows]
            _population_cache["matcher"] = TypologyMatcher().fit(signals, labels)
        except Exception as exc:  # pragma: no cover - typology is optional
            logger.warning("Typology matcher unavailable: %s", exc)
            _population_cache["matcher"] = None
    return _population_cache["matcher"]  # type: ignore[return-value]


def _transaction_evidence(txn: dict) -> ExecutorResult | None:
    """Build the history-free evidence block for one transaction."""
    if not txn:
        return None
    try:
        stats = _population_stats()
        signals = transaction_signals(_to_signal_row(txn), stats)
    except Exception as exc:  # pragma: no cover - never block an investigation
        logger.warning("Transaction-level evidence unavailable: %s", exc)
        return None

    # Only signals with measured lift are raised as risk indicators. Presenting
    # a neutral observation as evidence pushes the agents toward blocking, which
    # is what happened when this first shipped. Lift on the 313-row holdout,
    # against a 26.5% base rate:
    #
    #   DRAIN_RATIO >= 0.9        1.95x        DESTINATION_ABSORBS_ALL   0.99x
    #   FULL_BALANCE_DRAIN        1.86x        IP_UNSEEN_GLOBALLY        0.92x
    #   DORMANT_DESTINATION       1.85x        DEVICE_UNSEEN_GLOBALLY    0.87x
    #   AMOUNT_OUTLIER p95        1.57x
    #   AUTH_WEAKER_THAN_POLICY   1.28x
    #
    # The right-hand column carries no signal -- an unfamiliar device is
    # slightly *less* likely to be fraud here -- so those stay in `raw_data` as
    # features for the typology matcher and are not asserted as evidence.
    # Observations go into the narrative; only a judgement goes into
    # `risk_indicators`. report.py decides by counting indicators
    # (`len(risk_factors) >= 3 -> BLOCK`) with every entry weighted equally, so
    # emitting five separate observations blocks any transaction that merely
    # looks unusual. A large transfer on weak authentication is worth telling
    # the agents about; it is not five findings.
    observations: list[str] = []
    if signals["DRAIN_RATIO"] >= 0.9:
        observations.append(f"chuyển {signals['DRAIN_RATIO']:.0%} số dư khả dụng")
    if signals["FULL_BALANCE_DRAIN"]:
        observations.append("rút cạn số dư người gửi")
    if signals["DORMANT_DESTINATION"]:
        observations.append("tài khoản nhận có số dư 0 trước giao dịch")
    if signals["TXN_AMOUNT_PERCENTILE"] >= 0.95:
        observations.append(
            f"số tiền ở phân vị {signals['TXN_AMOUNT_PERCENTILE']:.0%} của toàn dân số"
        )
    if signals["AUTH_WEAKER_THAN_POLICY"] > 0:
        observations.append(
            f"xác thực {txn.get('auth_method')} yếu so với số tiền {txn.get('amount')}"
        )

    # The typology score is the aggregate over all of the above, so it is the
    # one finding worth raising. Prototypes are fitted only on labelled fraud
    # and carry no account identity, which is what lets this fire on an account
    # the bank has never seen.
    indicators: list[str] = []
    matcher = _typology_matcher()
    if matcher is not None:
        similarity = matcher.similarity(signals)
        signals["TYPOLOGY_MATCH"] = similarity
        if similarity >= 0.6:
            indicators.append(
                f"TYPOLOGY_MATCH: khớp {similarity:.0%} với hình dạng gian lận đã biết "
                f"({'; '.join(observations) or 'không có quan sát nổi bật'})"
            )

    analysis = "Bằng chứng mức giao dịch, không cần lịch sử tài khoản. "
    analysis += ("Quan sát: " + "; ".join(observations)) if observations else "Không có quan sát bất thường."

    return ExecutorResult(
        task_id="transaction_signals",
        task_type=TaskType.AMOUNT_PATTERN,
        success=True,
        raw_data=signals,
        analysis=analysis,
        risk_indicators=indicators,
    )


def _to_signal_row(txn: dict) -> dict:
    return {
        "amount": txn.get("amount"),
        "sender_balance_before": txn.get("sender_balance_before"),
        "sender_balance_after": txn.get("sender_balance_after"),
        "oldbalanceDest": txn.get("oldbalanceDest") or txn.get("receiver_balance_before"),
        "newbalanceDest": txn.get("newbalanceDest") or txn.get("receiver_balance_after"),
        "type": txn.get("transaction_type") or txn.get("type"),
        "auth_method": txn.get("auth_method"),
        "device_id": txn.get("device_id"),
        "ip_address": txn.get("ip_address"),
    }


# =====================================================================
# NODE: VISION (Gemini phân tích kết quả Executor)
# =====================================================================

def vision_node(state: GraphState) -> GraphState:
    """
    Vision Agent: Gemini đọc TẤT CẢ kết quả từ Executor.
    
    Cross-reference các evidence sources → phát hiện patterns ẩn.
    Trả VisionAnalysis về cho Planner đánh giá.
    """
    result_dicts = state.get("all_results", [])
    evidence = [ExecutorResult(**r) for r in result_dicts]
    
    # Lấy context cho Vision
    txn = state.get("transaction", {})
    phase1 = state.get("phase1_result", {})
    
    # FIX: Lấy hypothesis từ Planner (trước đây lấy nhầm "priority")
    hypothesis = _planner.hypothesis if _planner.hypothesis else ""
    
    investigation_context = {
        "transaction_id": txn.get("transaction_id", ""),
        "sender_id": txn.get("sender_id", ""),
        "receiver_id": txn.get("receiver_id", ""),
        "amount": txn.get("amount", 0),
        "initial_risk_score": phase1.get("risk_score", 0),
    }
    
    # Gọi Vision Agent (Gemini 2.5 Flash)
    analysis = vision_agent.analyze_results(
        evidence=evidence,
        hypothesis=hypothesis,
        investigation_context=investigation_context,
    )
    
    return {"vision_analysis": analysis}


# =====================================================================
# NODE: PLANNER EVALUATE (Planner đánh giá Vision analysis)
# =====================================================================

def planner_evaluate_node(state: GraphState) -> GraphState:
    """
    Planner nhận Vision analysis → quyết định:
    - Đủ evidence → done, chuyển sang Report
    - Chưa đủ → tạo follow-up tasks → quay lại Executor
    """
    result_dicts = state.get("all_results", [])
    evidence = [ExecutorResult(**r) for r in result_dicts]
    vision_analysis = state.get("vision_analysis", {})
    
    # FIX: Planner evaluate VỚI vision_analysis (trước đây bị bỏ qua)
    is_done, follow_up_tasks = _planner.evaluate_evidence(
        evidence, vision_analysis=vision_analysis
    )
    
    # Xem xét Vision recommendation
    vision_action = vision_analysis.get("recommended_action", "investigate_more")
    vision_risk = vision_analysis.get("overall_risk_level", "unknown")
    
    # Nếu Vision nói sufficient + planner cũng done → chắc chắn done
    # Nếu Vision nói investigate_more nhưng đã max steps → force done
    step = state.get("investigation_step", 1)
    max_steps = settings.max_investigation_steps
    
    if is_done or vision_action == "sufficient_for_report":
        print(f"   📋 PLANNER: Đủ evidence (vision: {vision_action}, risk: {vision_risk})")
        return {
            "investigation_done": True,
            "planner_confidence": _planner.current_confidence,
            "current_tasks": [],
        }
    elif step >= max_steps:
        print(f"   ⏰ PLANNER: Max steps ({max_steps}) reached, forcing report")
        return {
            "investigation_done": True,
            "planner_confidence": _planner.current_confidence,
            "current_tasks": [],
        }
    elif follow_up_tasks:
        print(f"   🔄 PLANNER: Cần thêm {len(follow_up_tasks)} tasks (step {step}/{max_steps})")
        return {
            "investigation_done": False,
            "planner_confidence": _planner.current_confidence,
            "current_tasks": [t.model_dump() for t in follow_up_tasks],
        }
    else:
        # No follow-up but not explicitly done → force done
        print(f"   📋 PLANNER: No follow-up tasks, proceeding to report")
        return {
            "investigation_done": True,
            "planner_confidence": _planner.current_confidence,
            "current_tasks": [],
        }


def route_after_evaluate(state: GraphState) -> str:
    """
    Routing sau evaluate:
    - done → report_generator
    - not done → executor (loop)
    """
    if state.get("investigation_done", False):
        return "report_generator"
    else:
        return "executor"


# =====================================================================
# NODE: REPORT GENERATOR (Gemini 2.5 Flash)
# =====================================================================

def current_transaction_evidence(state: GraphState) -> ExecutorResult | None:
    """Convert current transaction and Phase 1 signals into Phase 2 evidence."""

    txn = Transaction(**state["transaction"])
    phase1 = state.get("phase1_result", {}) or {}
    indicators: list[str] = []
    notes: list[str] = []

    for rule in phase1.get("triggered_rules", []):
        rule_name = rule.get("rule", "")
        detail = rule.get("detail", "")
        if rule_name == "BALANCE_DRAIN":
            indicators.append("CURRENT_BALANCE_DRAIN: sender balance drained in this transaction")
        elif rule_name in {"HIGH_VELOCITY", "SENDER_BLACKLISTED", "RECEIVER_BLACKLISTED"}:
            indicators.append(f"PHASE1_{rule_name}: {detail}")
        elif rule_name in {"LARGE_AMOUNT", "ELEVATED_AMOUNT", "STRUCTURING_SUSPICION"}:
            notes.append(f"{rule_name}: {detail}")

    if (
        txn.sender_balance_before is not None
        and txn.sender_balance_before > 0
        and txn.sender_balance_after is not None
        and txn.sender_balance_after <= 500
        and abs(txn.amount - txn.sender_balance_before) <= max(1.0, txn.sender_balance_before * 0.001)
    ):
        indicators.append(
            "FULL_BALANCE_TRANSFER: amount equals available sender balance and leaves sender near zero"
        )

    if (
        txn.transaction_type.upper() == "TRANSFER"
        and txn.receiver_balance_before is not None
        and txn.receiver_balance_after is not None
        and txn.receiver_balance_before == 0
        and txn.receiver_balance_after == 0
        and txn.sender_balance_after is not None
        and (txn.sender_balance_after <= 500 or txn.amount >= settings.large_amount_threshold)
    ):
        indicators.append(
            "DESTINATION_NOT_CREDITED: transfer drains sender while destination remains zero"
        )

    if not indicators and not notes:
        return None

    analysis = (
        f"Current transaction {txn.transaction_id}: {txn.transaction_type} "
        f"{txn.sender_id}->{txn.receiver_id} amount={txn.amount:,.2f}. "
    )
    if indicators:
        analysis += "Indicators: " + "; ".join(indicators) + ". "
    if notes:
        analysis += "Context: " + "; ".join(notes) + "."

    return ExecutorResult(
        task_id="current_transaction",
        task_type=TaskType.AMOUNT_PATTERN,
        success=True,
        raw_data={"transaction": txn.model_dump(), "phase1": phase1},
        analysis=analysis,
        risk_indicators=indicators,
    )


def report_generator_node(state: GraphState) -> GraphState:
    """
    Report Agent: generate investigation report bằng Gemini.
    """
    request_dict = state.get("investigation_request", {})
    result_dicts = state.get("all_results", [])
    evidence = [ExecutorResult(**r) for r in result_dicts]
    current_evidence = current_transaction_evidence(state)
    if current_evidence is not None:
        evidence.insert(0, current_evidence)
    
    request_id = request_dict.get("request_id", "unknown")
    txn_id = state.get("transaction", {}).get("transaction_id", "unknown")
    
    investigation_summary = _planner.get_investigation_summary()
    
    report = _report_agent.generate_report(
        request_id=request_id,
        transaction_id=txn_id,
        investigation_summary=investigation_summary,
        evidence=evidence,
    )
    
    return {"report": report.model_dump()}


# =====================================================================
# NODE: DETECTIVE (Final adjudication)
# =====================================================================

def detective_node(state: GraphState) -> GraphState:
    """
    Detective Agent: final adjudication bằng Gemini LLM.
    """
    report_dict = state.get("report", {})
    
    # Reconstruct ExecutorResult objects for evidence
    evidence_dicts = report_dict.get("evidence", [])
    evidence = []
    for ed in evidence_dicts:
        # Convert task_type string back to enum
        if isinstance(ed.get("task_type"), str):
            ed["task_type"] = TaskType(ed["task_type"])
        evidence.append(ExecutorResult(**ed))
    
    # Reconstruct recommended_decision
    rec_decision = report_dict.get("recommended_decision", "escalate")
    if isinstance(rec_decision, str):
        report_dict["recommended_decision"] = FinalDecision(rec_decision)
    
    report_dict["evidence"] = evidence
    report = InvestigationReport(**report_dict)
    
    # Pass sender_id từ transaction state làm fallback
    txn_sender_id = state.get("transaction", {}).get("sender_id", "")
    
    result = _detective.adjudicate(report, sender_id_fallback=txn_sender_id)
    
    return {
        "decision": result.model_dump(),
        "final_decision": result.decision.value,
        "final_message": (
            f"Transaction {result.transaction_id}: "
            f"{result.decision.value.upper()} "
            f"(confidence={result.confidence:.2f}). "
            f"{result.reasoning[:100]}"
        ),
    }


# =====================================================================
# BUILD LANGGRAPH PIPELINE
# =====================================================================

def build_pipeline() -> StateGraph:
    """
    Xây dựng LangGraph pipeline.
    
    Graph structure:
    
    START → phase1_screening
            ├── GREEN → end_allow → END
            ├── RED → end_block → END
            └── YELLOW → planner → executor → vision → planner_evaluate
                                                        ├── done → report → detective → END
                                                        └── not_done → executor (loop)
    """
    graph = StateGraph(GraphState)
    
    # ─── Add nodes ───
    graph.add_node("phase1_screening", phase1_screening)
    graph.add_node("end_allow", end_allow)
    graph.add_node("end_block", end_block)
    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_node)
    graph.add_node("vision", vision_node)
    graph.add_node("planner_evaluate", planner_evaluate_node)
    graph.add_node("report_generator", report_generator_node)
    graph.add_node("detective", detective_node)
    
    # ─── Set entry point ───
    graph.set_entry_point("phase1_screening")
    
    # ─── Conditional routing after Phase 1 ───
    graph.add_conditional_edges(
        "phase1_screening",
        route_after_phase1,
        {
            "end_allow": "end_allow",
            "end_block": "end_block",
            "planner": "planner",
        },
    )
    
    # ─── Green/Red paths → END ───
    graph.add_edge("end_allow", END)
    graph.add_edge("end_block", END)
    
    # ─── Investigation pipeline (NEW FLOW) ───
    # Planner → Executor → Vision → Planner Evaluate
    graph.add_edge("planner", "executor")
    graph.add_edge("executor", "vision")
    graph.add_edge("vision", "planner_evaluate")
    
    # ─── Conditional: planner_evaluate → report or loop ───
    graph.add_conditional_edges(
        "planner_evaluate",
        route_after_evaluate,
        {
            "report_generator": "report_generator",
            "executor": "executor",
        },
    )
    
    # ─── Report → Detective → END ───
    graph.add_edge("report_generator", "detective")
    graph.add_edge("detective", END)
    
    return graph


# =====================================================================
# ORCHESTRATOR CLASS (Wrapper for convenience)
# =====================================================================

class FraudDetectionOrchestrator:
    """
    High-level wrapper cho LangGraph pipeline.
    
    Dùng trong main.py:
        orchestrator = FraudDetectionOrchestrator()
        orchestrator.initialize()
        result = orchestrator.process_transaction(txn)
    """
    
    def __init__(self):
        self.graph = None
        self.app = None
        self._initialized = False
    
    def initialize(self):
        """
        Khởi tạo pipeline + seed demo data.
        
        Gọi 1 lần khi startup:
        - Seed Neo4j (nếu connected)
        - Seed ChromaDB knowledge base
        - Build LangGraph pipeline
        """
        if self._initialized:
            return
        
        print("\n" + "=" * 70)
        print(" FRAUD DETECTION SYSTEM - Initializing")
        print("=" * 70)
        
        # Hydrate in-memory fallback caches from the real dataset on every
        # startup. Cloud database writes are controlled separately below.
        stats = apply_processed_seed_to_simulators(
            redis_service=redis_service,
            redis_sim=redis_sim,
            dynamodb_sim=dynamodb_sim,
            neptune_sim=neptune_sim,
        )
        if stats.get("profiles"):
            print(
                "\nDataset fallback cache hydrated: "
                f"{stats['profiles']} profiles, {stats['transactions']} transactions, "
                f"{stats['edges']} edges"
            )

        # Cloud seed is intentionally opt-in because it can wipe/reseed remote DBs.
        if settings.auto_seed_on_startup:
            print("\n📊 Seeding databases...")
            redis_service.seed_data()
            neo4j_client.seed_demo_data()
            vector_store.seed_knowledge_base()
            mongodb_client.seed_demo_data()
        else:
            print("Database auto-seed skipped (AUTO_SEED_ON_STARTUP=false).")
        
        # ─── Build pipeline ───
        print("\n🔗 Building LangGraph pipeline...")
        self.graph = build_pipeline()
        self.app = self.graph.compile()
        
        self._initialized = True
        
        print("\n✅ System ready!")
        print(f"   Redis: {'Cloud (' + settings.redis_host + ')' if redis_service.is_connected else 'Simulator (in-memory)'}")
        print(f"   Neo4j: {'AuraDB (cloud)' if neo4j_client.is_connected else 'Simulator (in-memory)'}")
        print(f"   MongoDB: {'Atlas (cloud)' if mongodb_client.is_connected else 'Simulator (in-memory)'}")
        print(f"   LLM (ALL agents): Gemini {'(connected)' if settings.gemini_api_key and not settings.demo_mode else '(fallback)'}")
        print("=" * 70 + "\n")
    
    def process_transaction(self, transaction: Transaction) -> dict:
        """
        Xử lý 1 giao dịch qua pipeline LangGraph.
        
        Returns:
            dict với final_decision, final_message, và full state
        """
        if not self._initialized:
            self.initialize()
        
        print(f"\n{'*'*70}")
        print(f" PROCESSING: {transaction.transaction_id}")
        print(f" {transaction.sender_id} -> {transaction.receiver_id}: {transaction.amount:,.2f} {transaction.currency}")
        print(f"{'*'*70}")
        
        # ─── Run LangGraph pipeline (thread-safe) ───
        initial_state = make_initial_state(transaction)
        
        # FIX: Lock prevents concurrent access to shared agent instances
        with _processing_lock:
            _planner.reset()
            try:
                final_state = self.app.invoke(initial_state)
            except Exception as e:
                print(f"\n❌ Pipeline error: {e}")
                import traceback
                traceback.print_exc()
                final_state = {
                    **initial_state,
                    "final_decision": "escalate",
                    "final_message": f"Pipeline error: {str(e)}. Escalating to human review.",
                    "error": str(e),
                }
        
        # ─── Summary ───
        decision = final_state.get("final_decision", "escalate")
        message = final_state.get("final_message", "Unknown")
        
        symbols = {"allow": "✅", "block": "🚫", "escalate": "⚠️"}
        
        print(f"\n{'*'*70}")
        print(f" RESULT: {symbols.get(decision, '?')} {decision.upper()}")
        print(f" {message[:150]}")
        print(f"{'*'*70}")
        
        # ─── In báo cáo chi tiết (nếu có) ───
        report = final_state.get("report")
        if report and isinstance(report, dict):
            detailed = report.get("detailed_analysis", "")
            if detailed:
                print(f"\n{'─'*70}")
                print("📄 BÁO CÁO ĐIỀU TRA CHI TIẾT")
                print(f"{'─'*70}")
                print(detailed)
                print(f"{'─'*70}")
        
        print()
        
        # ─── Store audit trail to Redis (ref: fraud-detection/phase1.py._finalize) ───
        redis_service.store_transaction_result(transaction.transaction_id, {
            "decision": decision,
            "confidence": str((final_state.get("decision") or {}).get("confidence", 0)),
            "sender": transaction.sender_id,
            "receiver": transaction.receiver_id,
            "amount": str(transaction.amount),
            "timestamp": datetime.now().isoformat(),
        })
        
        return final_state
    
    def shutdown(self):
        """Cleanup khi tắt app — đóng tất cả DB connections."""
        neo4j_client.close()
        mongodb_client.close()
        print("🔌 System shutdown complete.")
