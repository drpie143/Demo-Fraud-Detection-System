# ====================================================================
# MAIN.PY - FastAPI Backend + CLI Demo
# ====================================================================
#
# THAY ĐỔI:
#   Cũ: CLI-only demo chạy 3 scenarios rồi thoát
#   Mới: FastAPI server + CLI mode
#
# MODES:
#   1. CLI Demo: python main.py
#      → Chạy 3 demo scenarios (GREEN, YELLOW, YELLOW-with-VPN)
#      → In kết quả ra terminal
#
#   2. API Server: python main.py --serve
#      → Khởi động FastAPI trên http://localhost:8000
#      → Endpoints: POST /transaction, GET /health, GET /scenarios
#
# DEMO SCENARIOS:
#   Scenario 1: ACC_001 → ACC_002 ($250)
#     → GREEN: sender whitelisted, low amount, low risk
#     → Expected: ALLOW (skip investigation)
#
#   Scenario 2: ACC_007 → ACC_002 ($950)
#     → YELLOW: velocity cao + amount gần threshold + risk score cao
#     → Expected: Investigation → likely BLOCK (structuring pattern)
#
#   Scenario 3: ACC_050 → ACC_666 ($25,000)
#     → YELLOW/RED: VPN + large amount + receiver blacklisted
#     → Expected: Investigation → BLOCK (money laundering)
# ====================================================================

from __future__ import annotations

import os
import sys
import json
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager

# Ensure backend package root is on sys.path
_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from models import Transaction
from orchestrator import FraudDetectionOrchestrator


# =====================================================================
# DEMO SCENARIOS
# =====================================================================

DEMO_SCENARIOS = [
    {
        "name": "Scenario 1: Normal Transaction (Expected: GREEN → ALLOW)",
        "description": (
            "ACC_001 (Nguyễn Văn An, whitelisted, 5 năm) "
            "gửi $250 cho ACC_002. Giao dịch bình thường."
        ),
        "transaction": Transaction(
            transaction_id="TXN_DEMO_001",
            timestamp=datetime.now().isoformat(),
            sender_id="ACC_001",
            sender_name="Nguyễn Văn An",
            sender_account_type="savings",
            receiver_id="ACC_002",
            receiver_name="Trần Minh Tuấn",
            amount=250.00,
            currency="USD",
            transaction_type="transfer",
            device_id="DEV_001",
            ip_address="14.161.42.100",
            channel="mobile",
            location="Ho Chi Minh City",
            description="Chuyển tiền ăn trưa",
        ),
    },
    {
        "name": "Scenario 2: Structuring Pattern (Expected: YELLOW → BLOCK)",
        "description": (
            "ACC_007 (Trần Thị B, tài khoản mới 45 ngày, velocity CAO) "
            "gửi $950 cho ACC_002. Nghi ngờ structuring: "
            "15 GD nhỏ (<$1000) trong 1 giờ qua, risk score cao."
        ),
        "transaction": Transaction(
            transaction_id="TXN_DEMO_002",
            timestamp=datetime.now().isoformat(),
            sender_id="ACC_007",
            sender_name="Trần Thị B",
            sender_account_type="checking",
            receiver_id="ACC_002",
            receiver_name="Trần Minh Tuấn",
            amount=950.00,
            currency="USD",
            transaction_type="transfer",
            device_id="DEV_002",
            ip_address="113.190.88.50",
            channel="mobile",
            location="Da Nang",
            description="Payment",
        ),
    },
    {
        "name": "Scenario 3: Money Laundering (Expected: YELLOW → BLOCK)",
        "description": (
            "ACC_050 (Unknown Entity, tài khoản mới 15 ngày, KYC pending) "
            "gửi $25,000 cho ACC_666 (BLACKLISTED). Dùng VPN/Tor."
        ),
        "transaction": Transaction(
            transaction_id="TXN_DEMO_003",
            timestamp=datetime.now().isoformat(),
            sender_id="ACC_050",
            sender_name="Unknown Entity",
            sender_account_type="business",
            receiver_id="ACC_666",
            receiver_name="Blocked Account",
            amount=25000.00,
            currency="USD",
            transaction_type="transfer",
            device_id="DEV_UNKNOWN_001",
            ip_address="185.220.101.42 (Tor Exit Node)",
            channel="web",
            location="Unknown (VPN)",
            merchant_id="MERCH_SHELL",
            description="Business payment",
        ),
    },
]


# =====================================================================
# CLI DEMO MODE
# =====================================================================

def run_cli_demo():
    """
    Chạy 3 demo scenarios liên tiếp.
    
    Output ra terminal với format đẹp, dễ đọc.
    Phù hợp cho hackathon presentation.
    """
    print("\n" + "=" * 70)
    print("  🏦 FRAUD DETECTION SYSTEM - Zero-Cost Agentic AI Demo")
    print("  " + "─" * 66)
    print("  Tech Stack:")
    print("    • LLM: Gemini 2.5 Flash (tất cả agents)")
    print("    • Graph DB: Neo4j AuraDB (cloud)")
    print("    • Vector Store: ChromaDB Cloud (trychroma.com)")
    print("    • Pipeline: LangGraph")
    print("    • Backend: FastAPI")
    print("=" * 70)
    
    orchestrator = FraudDetectionOrchestrator()
    orchestrator.initialize()
    
    results = []
    
    for i, scenario in enumerate(DEMO_SCENARIOS, 1):
        print(f"\n{'█'*70}")
        print(f"█ DEMO {i}/3: {scenario['name']}")
        print(f"█ {scenario['description'][:100]}")
        print(f"{'█'*70}")
        
        result = orchestrator.process_transaction(scenario["transaction"])
        results.append({
            "scenario": scenario["name"],
            "decision": result.get("final_decision", "unknown"),
            "message": result.get("final_message", ""),
        })
        
        print(f"\n{'─'*70}")
        input("   ⏎ Press Enter to continue to next scenario...") if i < len(DEMO_SCENARIOS) else None
    
    # ─── Summary ───
    print(f"\n{'='*70}")
    print("  📊 DEMO SUMMARY")
    print(f"{'='*70}")
    
    for r in results:
        symbols = {"allow": "✅", "block": "🚫", "escalate": "⚠️"}
        s = symbols.get(r["decision"], "?")
        print(f"  {s} {r['scenario'][:50]}")
        print(f"     → {r['decision'].upper()}: {r['message'][:80]}")
    
    print(f"\n{'='*70}")
    print("  Demo hoàn tất! 🎉")
    print(f"{'='*70}\n")
    
    orchestrator.shutdown()


# =====================================================================
# FASTAPI SERVER MODE
# =====================================================================

def create_fastapi_app():
    """
    Tạo FastAPI application.
    
    Endpoints:
    - GET  /        → API info
    - GET  /health  → Health check
    - POST /transaction → Process a transaction
    - GET  /scenarios   → List demo scenarios
    - POST /demo/{n}    → Run demo scenario N (1-3)
    """
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    
    # Shared orchestrator
    orchestrator = FraudDetectionOrchestrator()
    
    # FIX: Dùng lifespan thay vì deprecated on_event
    @asynccontextmanager
    async def lifespan(app):
        orchestrator.initialize()
        yield
        orchestrator.shutdown()
    
    app = FastAPI(
        title="Fraud Detection System",
        description="Zero-Cost Agentic AI Fraud Detection Pipeline",
        version="2.0.0",
        lifespan=lifespan,
    )
    
    # CORS — cho phép localhost + production frontend URL từ env
    cors_origins = ["http://localhost:3000", "http://localhost:8000", "http://127.0.0.1:3000"]
    frontend_url = os.environ.get("FRONTEND_URL", "")
    if frontend_url:
        cors_origins.append(frontend_url)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    @app.get("/")
    async def root():
        return {
            "system": "Fraud Detection System",
            "version": "2.0.0",
            "stack": {
                "llm": "Gemini 2.5 Flash (all agents)",
                "graph_db": "Neo4j AuraDB",
                "vector_store": "ChromaDB",
                "pipeline": "LangGraph",
                "backend": "FastAPI",
            },
            "endpoints": {
                "health": "GET /health",
                "process": "POST /transaction",
                "scenarios": "GET /scenarios",
                "demo": "POST /demo/{scenario_number}",
            },
        }
    
    @app.get("/health")
    async def health():
        from database.graph_db import neo4j_client
        from config import settings as _settings
        return {
            "status": "healthy",
            "neo4j": "connected" if neo4j_client.is_connected else "simulator",
            "chromadb": "active",
            "gemini": "configured" if _settings.gemini_api_key else "fallback",
        }
    
    @app.post("/transaction")
    async def process_transaction(transaction: Transaction):
        """
        Xử lý 1 giao dịch qua pipeline.
        
        Body: Transaction object (JSON)
        Returns: Full pipeline result
        
        FIX: Dùng asyncio.to_thread để không block event loop
        (pipeline có thể mất 30-60s cho LLM calls).
        """
        result = await asyncio.to_thread(
            orchestrator.process_transaction, transaction
        )
        
        return {
            "transaction_id": transaction.transaction_id,
            "decision": result.get("final_decision", "escalate"),
            "message": result.get("final_message", ""),
            "phase1": result.get("phase1_result"),
            "investigation": {
                "steps": result.get("investigation_step", 0),
                "evidence_count": len(result.get("all_results", [])),
                "confidence": result.get("planner_confidence", 0),
            },
            "report": result.get("report"),
            "detail": result.get("decision"),
        }
    
    # =================================================================
    # API ENDPOINTS - Frontend Integration
    # =================================================================
    
    @app.post("/api/login")
    async def api_login(credentials: dict):
        """
        Login endpoint cho frontend.
        
        Body: {"username": "ACC_001", "password": "any"}
        
        Validate username = account_id trong customer_profiles
        (MongoDB Atlas hoặc DynamoDBSimulator fallback).
        Password chấp nhận bất kỳ giá trị nào (demo mode).
        
        Returns: Account info (id, name, balance, account_type)
        """
        from database.mongo_db import mongodb_client
        
        username = credentials.get("username", "").strip()
        password = credentials.get("password", "")
        
        if not username or not password:
            raise HTTPException(
                status_code=400,
                detail="Username và password không được để trống",
            )
        
        # Tìm customer profile theo username (= account_id)
        profile = mongodb_client.get_customer_profile(username)
        
        # Kiểm tra tài khoản có tồn tại không
        if profile.get("kyc_status") == "not_found" or profile.get("name") == "Unknown":
            raise HTTPException(
                status_code=401,
                detail=f"Tài khoản '{username}' không tồn tại trong hệ thống",
            )
        
        # Tạo danh sách accounts cho user (checking, savings, business)
        # Demo: tạo balance giả dựa trên avg_transaction_amount
        avg_amount = profile.get("avg_transaction_amount", 500)
        account_type = profile.get("account_type", "checking")
        
        accounts = [
            {
                "id": profile.get("customer_id", username),
                "name": f"{account_type.capitalize()} Account",
                "balance": round(avg_amount * 10, 2),
                "type": account_type,
            },
        ]
        
        return {
            "success": True,
            "user": {
                "id": profile.get("customer_id", username),
                "name": profile.get("name", "Unknown"),
                "kyc_status": profile.get("kyc_status", "unknown"),
                "risk_category": profile.get("risk_category", "unknown"),
            },
            "accounts": accounts,
        }
    
    @app.post("/api/fraud-detection")
    async def api_fraud_detection_stream(payload: dict):
        """
        Fraud detection endpoint với SSE streaming.
        
        Gửi progress events real-time từng phase:
          {"event":"phase1_start"}
          {"event":"phase1_done","data":{...}}
          {"event":"phase2_start"}
          {"event":"phase2_progress","data":{...}}
          {"event":"phase3_start"}
          {"event":"phase3_done","data":{...}}
          {"event":"complete","data":{...}}
        """
        from starlette.responses import StreamingResponse
        from database.mongo_db import mongodb_client
        from orchestrator import (
            GraphState, _processing_lock, _planner,
            phase1_screening, route_after_phase1,
            end_allow, end_block,
            planner_node, executor_node, vision_node,
            planner_evaluate_node, route_after_evaluate,
            report_generator_node, detective_node,
        )
        import json as json_mod
        import traceback
        
        account_id = payload.get("account_id", "")
        amount = float(payload.get("amount", 0))
        recipient_id = payload.get("recipient_id", "")
        description = payload.get("description", "")
        timestamp = payload.get("timestamp", datetime.now().isoformat())
        
        if not account_id or not recipient_id or amount <= 0:
            # Trả error luôn, không stream
            raise HTTPException(
                status_code=400,
                detail="account_id, recipient_id, và amount (> 0) là bắt buộc",
            )
        
        # Lấy profile sender + receiver
        sender_profile = mongodb_client.get_customer_profile(account_id)
        receiver_profile = mongodb_client.get_customer_profile(recipient_id)
        
        txn_id = f"TXN_WEB_{account_id}_{int(datetime.now().timestamp())}"
        transaction = Transaction(
            transaction_id=txn_id,
            timestamp=timestamp,
            sender_id=account_id,
            sender_name=sender_profile.get("name", "Unknown"),
            sender_account_type=sender_profile.get("account_type", "checking"),
            receiver_id=recipient_id,
            receiver_name=receiver_profile.get("name", "Unknown"),
            amount=amount,
            currency="USD",
            transaction_type="transfer",
            channel="web",
            description=description,
        )
        
        async def event_generator():
            """Generator SSE events từng phase."""

            def send_event(event_name: str, data: dict = None):
                """Format SSE event line."""
                payload_obj = {"event": event_name}
                if data:
                    payload_obj["data"] = data
                return f"data: {json_mod.dumps(payload_obj, default=str)}\n\n"

            try:
                # ─── INGEST: Insert transaction into all DBs before pipeline ───
                from database.graph_db import neo4j_client
                try:
                    mongodb_client.ingest_transaction(
                        sender_id=account_id,
                        receiver_id=recipient_id,
                        amount=amount,
                        sender_name=sender_profile.get("name", ""),
                        receiver_name=receiver_profile.get("name", ""),
                        description=description,
                        channel="web",
                    )
                    neo4j_client.ingest_transaction(
                        sender_id=account_id,
                        receiver_id=recipient_id,
                        amount=amount,
                        sender_name=sender_profile.get("name", ""),
                        receiver_name=receiver_profile.get("name", ""),
                    )
                    # Increment velocity counter
                    from orchestrator import redis_service
                    redis_service.increment_velocity(account_id)
                    print(f"   ✅ Transaction ingested into all DBs: {account_id} → {recipient_id} (${amount:,.2f})")
                except Exception as ingest_err:
                    print(f"   ⚠️  Ingest warning (non-fatal): {ingest_err}")

                # ─── PHASE 1: Screening ───
                yield send_event("phase1_start")
                
                initial_state: GraphState = {
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
                
                # Chạy Phase 1
                with _processing_lock:
                    _planner.reset()
                    phase1_update = await asyncio.to_thread(
                        phase1_screening, initial_state
                    )
                
                state = {**initial_state, **phase1_update}
                risk_level = state.get("phase1_risk_level", "yellow")
                
                yield send_event("phase1_done", {
                    "phase1": state.get("phase1_result"),
                    "risk_level": risk_level,
                })
                
                # ─── ROUTING ───
                route = route_after_phase1(state)
                
                if route == "end_allow":
                    # GREEN → allow ngay
                    with _processing_lock:
                        allow_update = await asyncio.to_thread(end_allow, state)
                    state = {**state, **allow_update}
                    
                    yield send_event("complete", {
                        "transaction_id": txn_id,
                        "decision": state.get("final_decision", "allow"),
                        "message": state.get("final_message", ""),
                        "phase1": state.get("phase1_result"),
                        "investigation": {"steps": 0, "evidence_count": 0, "confidence": 0},
                        "report": None,
                        "detail": state.get("decision"),
                    })
                    return
                
                elif route == "end_block":
                    # RED → block ngay
                    with _processing_lock:
                        block_update = await asyncio.to_thread(end_block, state)
                    state = {**state, **block_update}
                    
                    yield send_event("complete", {
                        "transaction_id": txn_id,
                        "decision": state.get("final_decision", "block"),
                        "message": state.get("final_message", ""),
                        "phase1": state.get("phase1_result"),
                        "investigation": {"steps": 0, "evidence_count": 0, "confidence": 0},
                        "report": None,
                        "detail": state.get("decision"),
                    })
                    return
                
                # ─── YELLOW → PHASE 2: Investigation ───
                yield send_event("phase2_start", {"risk_level": "yellow"})
                
                # Planner
                yield send_event("phase2_progress", {"agent": "Planner", "status": "planning"})
                with _processing_lock:
                    planner_update = await asyncio.to_thread(planner_node, state)
                state = {**state, **planner_update}
                yield send_event("phase2_progress", {
                    "agent": "Planner",
                    "status": "done",
                    "tasks": len(state.get("current_tasks", [])),
                })
                
                # Investigation loop: Executor → Vision → Evaluate
                max_loops = 3
                for loop_i in range(max_loops):
                    # Executor
                    yield send_event("phase2_progress", {
                        "agent": "Executor",
                        "status": "executing",
                        "step": loop_i + 1,
                    })
                    with _processing_lock:
                        exec_update = await asyncio.to_thread(executor_node, state)
                    state = {**state, **exec_update}
                    yield send_event("phase2_progress", {
                        "agent": "Executor",
                        "status": "done",
                        "evidence_count": len(state.get("all_results", [])),
                    })
                    
                    # Vision
                    yield send_event("phase2_progress", {
                        "agent": "Vision",
                        "status": "analyzing",
                    })
                    with _processing_lock:
                        vision_update = await asyncio.to_thread(vision_node, state)
                    state = {**state, **vision_update}
                    yield send_event("phase2_progress", {
                        "agent": "Vision",
                        "status": "done",
                    })
                    
                    # Planner Evaluate
                    yield send_event("phase2_progress", {
                        "agent": "Planner Evaluate",
                        "status": "evaluating",
                    })
                    with _processing_lock:
                        eval_update = await asyncio.to_thread(
                            planner_evaluate_node, state
                        )
                    state = {**state, **eval_update}
                    
                    eval_route = route_after_evaluate(state)
                    if eval_route == "report_generator":
                        yield send_event("phase2_progress", {
                            "agent": "Planner Evaluate",
                            "status": "done",
                            "result": "sufficient_evidence",
                        })
                        break
                    else:
                        yield send_event("phase2_progress", {
                            "agent": "Planner Evaluate",
                            "status": "need_more",
                            "step": loop_i + 1,
                        })
                
                yield send_event("phase2_done", {
                    "investigation": {
                        "steps": state.get("investigation_step", 0),
                        "evidence_count": len(state.get("all_results", [])),
                        "confidence": state.get("planner_confidence", 0),
                    },
                })
                
                # ─── PHASE 3: Report + Detective ───
                yield send_event("phase3_start")
                
                # Report
                yield send_event("phase3_progress", {
                    "agent": "Report Generator",
                    "status": "generating",
                })
                with _processing_lock:
                    report_update = await asyncio.to_thread(
                        report_generator_node, state
                    )
                state = {**state, **report_update}
                yield send_event("phase3_progress", {
                    "agent": "Report Generator",
                    "status": "done",
                })
                
                # Detective
                yield send_event("phase3_progress", {
                    "agent": "Detective",
                    "status": "deciding",
                })
                with _processing_lock:
                    detective_update = await asyncio.to_thread(
                        detective_node, state
                    )
                state = {**state, **detective_update}
                
                # Store audit trail
                from orchestrator import redis_service
                redis_service.store_transaction_result(transaction.transaction_id, {
                    "decision": state.get("final_decision", "escalate"),
                    "confidence": str(state.get("decision", {}).get("confidence", 0)),
                    "sender": transaction.sender_id,
                    "receiver": transaction.receiver_id,
                    "amount": str(transaction.amount),
                    "timestamp": datetime.now().isoformat(),
                })
                
                yield send_event("phase3_done", {
                    "decision": state.get("final_decision", "escalate"),
                    "detail": state.get("decision"),
                    "report": state.get("report"),
                })
                
                # Final complete event
                yield send_event("complete", {
                    "transaction_id": txn_id,
                    "decision": state.get("final_decision", "escalate"),
                    "message": state.get("final_message", ""),
                    "phase1": state.get("phase1_result"),
                    "investigation": {
                        "steps": state.get("investigation_step", 0),
                        "evidence_count": len(state.get("all_results", [])),
                        "confidence": state.get("planner_confidence", 0),
                    },
                    "report": state.get("report"),
                    "detail": state.get("decision"),
                })
                
            except Exception as e:
                print(f"\n❌ Streaming pipeline error: {e}")
                traceback.print_exc()
                yield send_event("error", {"message": str(e)})
                yield send_event("complete", {
                    "transaction_id": txn_id,
                    "decision": "escalate",
                    "message": f"Pipeline error: {str(e)}",
                    "phase1": state.get("phase1_result") if 'state' in locals() else None,
                    "investigation": {"steps": 0, "evidence_count": 0, "confidence": 0},
                    "report": None,
                    "detail": None,
                })
        
        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    
    @app.get("/scenarios")
    async def list_scenarios():
        """Liệt kê các demo scenarios."""
        return [
            {
                "id": i + 1,
                "name": s["name"],
                "description": s["description"],
                "transaction": s["transaction"].model_dump(),
            }
            for i, s in enumerate(DEMO_SCENARIOS)
        ]
    
    @app.post("/demo/{scenario_number}")
    async def run_demo_scenario(scenario_number: int):
        """
        Chạy 1 demo scenario (1-3).
        
        FIX: Dùng asyncio.to_thread để không block event loop.
        """
        if scenario_number < 1 or scenario_number > len(DEMO_SCENARIOS):
            raise HTTPException(
                status_code=400,
                detail=f"Scenario number must be 1-{len(DEMO_SCENARIOS)}",
            )
        
        scenario = DEMO_SCENARIOS[scenario_number - 1]
        result = await asyncio.to_thread(
            orchestrator.process_transaction, scenario["transaction"]
        )
        
        return {
            "scenario": scenario["name"],
            "description": scenario["description"],
            "transaction_id": scenario["transaction"].transaction_id,
            "decision": result.get("final_decision", "escalate"),
            "message": result.get("final_message", ""),
            "phase1": result.get("phase1_result"),
            "report": result.get("report"),
            "detail": result.get("decision"),
        }
    
    return app


# =====================================================================
# ENTRY POINT
# =====================================================================

def main():
    """
    Entry point:
    - python main.py          → CLI demo (3 scenarios)
    - python main.py --serve  → FastAPI server
    """
    if "--serve" in sys.argv:
        # ─── FastAPI Server Mode ───
        import uvicorn
        from config import settings
        
        print("\n🚀 Starting FastAPI server...")
        print(f"   URL: http://{settings.api_host}:{settings.api_port}")
        print(f"   Docs: http://localhost:{settings.api_port}/docs")
        
        app = create_fastapi_app()
        uvicorn.run(
            app,
            host=settings.api_host,
            port=settings.api_port,
            log_level="info",
        )
    else:
        # ─── CLI Demo Mode ───
        run_cli_demo()


if __name__ == "__main__":
    main()
