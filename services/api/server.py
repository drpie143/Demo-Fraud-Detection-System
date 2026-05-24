from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from contextlib import asynccontextmanager
from datetime import datetime

from core.orchestration.pipeline import FraudDetectionOrchestrator
from core.schemas.models import Transaction
from infrastructure.databases.seed_loader import build_demo_scenarios, dataset_summary


DEMO_SCENARIOS = build_demo_scenarios()
if not DEMO_SCENARIOS:
    print("WARNING: No dataset demo scenarios found. /scenarios will be empty.")


def run_cli_demo() -> None:
    """Run the dataset-backed demo scenarios in the terminal."""

    print("\n" + "=" * 70)
    print("  FRAUD DETECTION SYSTEM - Dataset-backed demo")
    print("=" * 70)

    orchestrator = FraudDetectionOrchestrator()
    orchestrator.initialize()

    results: list[dict[str, str]] = []

    try:
        for index, scenario in enumerate(DEMO_SCENARIOS, 1):
            transaction = scenario["transaction"]
            print(f"\n{'=' * 70}")
            print(f"DEMO {index}/{len(DEMO_SCENARIOS)}: {scenario['name']}")
            print(f"{transaction.sender_id} -> {transaction.receiver_id} | {transaction.amount:,.2f} {transaction.currency}")
            print(f"Expected: {scenario.get('expected_decision', 'review').upper()}")
            print("=" * 70)

            result = orchestrator.process_transaction(transaction)
            results.append(
                {
                    "scenario": scenario["name"],
                    "decision": result.get("final_decision", "unknown"),
                    "message": result.get("final_message", ""),
                }
            )

            if index < len(DEMO_SCENARIOS):
                input("Press Enter to continue...")

        print("\n" + "=" * 70)
        print("DEMO SUMMARY")
        print("=" * 70)
        for result in results:
            print(f"{result['decision'].upper():8} {result['scenario']}")
            print(f"         {result['message']}")
    finally:
        orchestrator.shutdown()


def create_fastapi_app():
    """Create the FastAPI application used by the CLI and frontend."""

    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware

    orchestrator = FraudDetectionOrchestrator()

    @asynccontextmanager
    async def lifespan(app):
        orchestrator.initialize()
        yield
        orchestrator.shutdown()

    app = FastAPI(
        title="Fraud Detection System",
        description="Dataset-backed agentic fraud detection pipeline",
        version="2.0.0",
        lifespan=lifespan,
    )

    cors_origins = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
    ]
    configured_origins = ",".join(
        value
        for value in [
            os.environ.get("FRONTEND_URL", ""),
            os.environ.get("FRONTEND_URLS", ""),
        ]
        if value
    )
    for origin in configured_origins.split(","):
        origin = origin.strip().rstrip("/")
        if origin and origin not in cors_origins:
            cors_origins.append(origin)

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
                "llm": "Gemini with deterministic fallback",
                "graph_db": "Neo4j or simulator",
                "vector_store": "ChromaDB or simulator",
                "pipeline": "LangGraph",
                "backend": "FastAPI",
            },
            "endpoints": {
                "health": "GET /health",
                "process": "POST /transaction",
                "scenarios": "GET /api/scenarios",
                "demo": "POST /demo/{scenario_number}",
            },
            "dataset": dataset_summary(),
        }

    @app.get("/health")
    async def health():
        from configs.settings import settings as runtime_settings
        from infrastructure.databases.chroma import vector_store
        from infrastructure.databases.mongodb import mongodb_client
        from infrastructure.databases.neo4j import neo4j_client
        from infrastructure.databases.simulators import redis_service

        return {
            "status": "healthy",
            "mode": "real_services" if not runtime_settings.demo_mode else "demo_simulator",
            "demo_mode": runtime_settings.demo_mode,
            "auto_seed_on_startup": runtime_settings.auto_seed_on_startup,
            "services": {
                "redis": "connected" if redis_service.is_connected else "simulator",
                "neo4j": "connected" if neo4j_client.is_connected else "simulator",
                "mongodb": "connected" if mongodb_client.is_connected else "simulator",
                "chromadb": "connected" if vector_store.collection else "simulator",
                "gemini": (
                    "configured"
                    if (
                        not runtime_settings.demo_mode
                        and any(
                            [
                                runtime_settings.gemini_api_key,
                                runtime_settings.gemini_api_key_planner,
                                runtime_settings.gemini_api_key_executor,
                                runtime_settings.gemini_api_key_executor_pool,
                                runtime_settings.gemini_api_key_detective,
                                runtime_settings.gemini_api_key_vision,
                                runtime_settings.gemini_api_key_report,
                            ]
                        )
                    )
                    else "fallback"
                ),
            },
            "service_errors": {
                "mongodb": (
                    mongodb_client.last_error[:700]
                    if not mongodb_client.is_connected
                    else ""
                ),
            },
            "neo4j": "connected" if neo4j_client.is_connected else "simulator",
            "chromadb": "connected" if vector_store.collection else "simulator",
            "gemini": (
                "configured"
                if (
                    not runtime_settings.demo_mode
                    and any(
                        [
                            runtime_settings.gemini_api_key,
                            runtime_settings.gemini_api_key_planner,
                            runtime_settings.gemini_api_key_executor,
                            runtime_settings.gemini_api_key_executor_pool,
                            runtime_settings.gemini_api_key_detective,
                            runtime_settings.gemini_api_key_vision,
                            runtime_settings.gemini_api_key_report,
                        ]
                    )
                )
                else "fallback"
            ),
            "dataset": dataset_summary(),
        }

    @app.post("/transaction")
    async def process_transaction(transaction: Transaction):
        result = await asyncio.to_thread(orchestrator.process_transaction, transaction)
        return {
            "transaction_id": transaction.transaction_id,
            "decision": result.get("final_decision", "escalate"),
            "message": result.get("final_message", ""),
            "phase1": result.get("phase1_result"),
            "risk_level": result.get("phase1_risk_level"),
            "investigation": {
                "steps": result.get("investigation_step", 0),
                "evidence_count": len(result.get("all_results", [])),
                "confidence": result.get("planner_confidence", 0),
            },
            "report": result.get("report"),
            "detail": result.get("decision"),
        }

    @app.post("/api/login")
    async def api_login(credentials: dict):
        from infrastructure.databases.mongodb import mongodb_client

        username = credentials.get("username", "").strip()
        password = credentials.get("password", "")

        if not username or not password:
            raise HTTPException(status_code=400, detail="Username and password are required")

        profile = mongodb_client.get_customer_profile(username)
        if profile.get("name") == "Unknown" or not profile.get("name"):
            raise HTTPException(status_code=401, detail=f"Account '{username}' was not found in the dataset")

        avg_amount = profile.get("avg_transaction_amount", 500)
        account_type = profile.get("account_type", "checking")

        return {
            "success": True,
            "user": {
                "id": profile.get("customer_id", username),
                "name": profile.get("name", "Unknown"),
                "kyc_status": profile.get("kyc_status", "verified" if profile.get("risk_category") == "low" else "review"),
                "risk_category": profile.get("risk_category", "unknown"),
                "fraud_ratio": profile.get("fraud_ratio", 0),
            },
            "accounts": [
                {
                    "id": profile.get("customer_id", username),
                    "name": f"{account_type.capitalize()} Account",
                    "balance": round(float(avg_amount) * 10, 2),
                    "type": account_type,
                }
            ],
        }

    @app.post("/api/fraud-detection")
    async def api_fraud_detection_stream(payload: dict):
        from starlette.responses import StreamingResponse

        from core.orchestration.pipeline import (
            _planner,
            _processing_lock,
            detective_node,
            end_allow,
            end_block,
            executor_node,
            make_initial_state,
            phase1_screening,
            planner_evaluate_node,
            planner_node,
            report_generator_node,
            route_after_evaluate,
            route_after_phase1,
            vision_node,
        )
        from infrastructure.databases.mongodb import mongodb_client

        account_id = str(payload.get("account_id", "")).strip()
        recipient_id = str(payload.get("recipient_id", "")).strip()
        try:
            amount = float(payload.get("amount", 0))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="amount must be a number") from None
        description = payload.get("description", "")
        timestamp = payload.get("timestamp", datetime.now().isoformat())
        currency = payload.get("currency", "VND")
        device_id = payload.get("device_id", "")
        ip_address = payload.get("ip_address", "")
        auth_method = payload.get("auth_method", "")
        sender_balance_before = payload.get("sender_balance_before")
        sender_balance_after = payload.get("sender_balance_after")

        if not account_id or not recipient_id or amount <= 0:
            raise HTTPException(status_code=400, detail="account_id, recipient_id, and amount > 0 are required")

        sender_profile = mongodb_client.get_customer_profile(account_id)
        receiver_profile = mongodb_client.get_customer_profile(recipient_id)

        if not device_id:
            device_id = sender_profile.get("device_id", "")
        if not ip_address:
            ip_address = sender_profile.get("ip_address", "")

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
            currency=currency,
            transaction_type="transfer",
            channel="web",
            description=description,
            device_id=device_id,
            ip_address=ip_address,
            sender_balance_before=sender_balance_before,
            sender_balance_after=sender_balance_after,
            auth_method=auth_method,
        )

        def send_event(event_name: str, data: dict | None = None) -> str:
            body = {"event": event_name}
            if data is not None:
                body["data"] = data
            return f"data: {json.dumps(body, default=str)}\n\n"

        async def event_generator():
            state = None
            try:
                from infrastructure.databases.neo4j import neo4j_client

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
                        device_id=device_id,
                        ip_address=ip_address,
                    )
                except Exception as ingest_err:
                    print(f"Ingest warning (non-fatal): {ingest_err}")

                yield send_event("phase1_start")
                initial_state = make_initial_state(transaction)

                with _processing_lock:
                    _planner.reset()
                    phase1_update = await asyncio.to_thread(phase1_screening, initial_state)

                state = {**initial_state, **phase1_update}
                risk_level = state.get("phase1_risk_level", "yellow")

                yield send_event(
                    "phase1_done",
                    {
                        "phase1": state.get("phase1_result"),
                        "risk_level": risk_level,
                    },
                )

                route = route_after_phase1(state)

                if route == "end_allow":
                    with _processing_lock:
                        allow_update = await asyncio.to_thread(end_allow, state)
                    state = {**state, **allow_update}
                    yield send_event(
                        "complete",
                        {
                            "transaction_id": txn_id,
                            "decision": state.get("final_decision", "allow"),
                            "message": state.get("final_message", ""),
                            "phase1": state.get("phase1_result"),
                            "risk_level": state.get("phase1_risk_level"),
                            "investigation": {"steps": 0, "evidence_count": 0, "confidence": 0},
                            "report": None,
                            "detail": state.get("decision"),
                        },
                    )
                    return

                if route == "end_block":
                    with _processing_lock:
                        block_update = await asyncio.to_thread(end_block, state)
                    state = {**state, **block_update}
                    yield send_event(
                        "complete",
                        {
                            "transaction_id": txn_id,
                            "decision": state.get("final_decision", "block"),
                            "message": state.get("final_message", ""),
                            "phase1": state.get("phase1_result"),
                            "risk_level": state.get("phase1_risk_level"),
                            "investigation": {"steps": 0, "evidence_count": 0, "confidence": 0},
                            "report": None,
                            "detail": state.get("decision"),
                        },
                    )
                    return

                yield send_event("phase2_start", {"risk_level": risk_level})

                yield send_event("phase2_progress", {"agent": "Planner", "status": "planning"})
                with _processing_lock:
                    planner_update = await asyncio.to_thread(planner_node, state)
                state = {**state, **planner_update}
                yield send_event(
                    "phase2_progress",
                    {
                        "agent": "Planner",
                        "status": "done",
                        "tasks": len(state.get("current_tasks", [])),
                    },
                )

                for loop_index in range(3):
                    yield send_event(
                        "phase2_progress",
                        {"agent": "Executor", "status": "executing", "step": loop_index + 1},
                    )
                    with _processing_lock:
                        exec_update = await asyncio.to_thread(executor_node, state)
                    state = {**state, **exec_update}
                    yield send_event(
                        "phase2_progress",
                        {
                            "agent": "Executor",
                            "status": "done",
                            "evidence_count": len(state.get("all_results", [])),
                        },
                    )

                    yield send_event("phase2_progress", {"agent": "Vision", "status": "analyzing"})
                    with _processing_lock:
                        vision_update = await asyncio.to_thread(vision_node, state)
                    state = {**state, **vision_update}
                    yield send_event("phase2_progress", {"agent": "Vision", "status": "done"})

                    yield send_event("phase2_progress", {"agent": "Planner Evaluate", "status": "evaluating"})
                    with _processing_lock:
                        eval_update = await asyncio.to_thread(planner_evaluate_node, state)
                    state = {**state, **eval_update}

                    if route_after_evaluate(state) == "report_generator":
                        yield send_event(
                            "phase2_progress",
                            {
                                "agent": "Planner Evaluate",
                                "status": "done",
                                "result": "sufficient_evidence",
                            },
                        )
                        break

                    yield send_event(
                        "phase2_progress",
                        {"agent": "Planner Evaluate", "status": "need_more", "step": loop_index + 1},
                    )

                yield send_event(
                    "phase2_done",
                    {
                        "investigation": {
                            "steps": state.get("investigation_step", 0),
                            "evidence_count": len(state.get("all_results", [])),
                            "confidence": state.get("planner_confidence", 0),
                        },
                    },
                )

                yield send_event("phase3_start")

                yield send_event("phase3_progress", {"agent": "Report Generator", "status": "generating"})
                with _processing_lock:
                    report_update = await asyncio.to_thread(report_generator_node, state)
                state = {**state, **report_update}
                yield send_event("phase3_progress", {"agent": "Report Generator", "status": "done"})

                yield send_event("phase3_progress", {"agent": "Detective", "status": "deciding"})
                with _processing_lock:
                    detective_update = await asyncio.to_thread(detective_node, state)
                state = {**state, **detective_update}

                from infrastructure.databases.simulators import redis_service

                redis_service.store_transaction_result(
                    transaction.transaction_id,
                    {
                        "decision": state.get("final_decision", "escalate"),
                        "confidence": str(state.get("decision", {}).get("confidence", 0)),
                        "sender": transaction.sender_id,
                        "receiver": transaction.receiver_id,
                        "amount": str(transaction.amount),
                        "timestamp": datetime.now().isoformat(),
                    },
                )

                yield send_event(
                    "phase3_done",
                    {
                        "decision": state.get("final_decision", "escalate"),
                        "detail": state.get("decision"),
                        "report": state.get("report"),
                    },
                )

                yield send_event(
                    "complete",
                    {
                        "transaction_id": txn_id,
                        "decision": state.get("final_decision", "escalate"),
                        "message": state.get("final_message", ""),
                        "phase1": state.get("phase1_result"),
                        "risk_level": state.get("phase1_risk_level"),
                        "investigation": {
                            "steps": state.get("investigation_step", 0),
                            "evidence_count": len(state.get("all_results", [])),
                            "confidence": state.get("planner_confidence", 0),
                        },
                        "report": state.get("report"),
                        "detail": state.get("decision"),
                    },
                )
            except Exception as exc:
                print(f"Streaming pipeline error: {exc}")
                traceback.print_exc()
                yield send_event("error", {"message": str(exc)})
                yield send_event(
                    "complete",
                    {
                        "transaction_id": txn_id,
                        "decision": "escalate",
                        "message": f"Pipeline error: {exc}",
                        "phase1": state.get("phase1_result") if state else None,
                        "investigation": {"steps": 0, "evidence_count": 0, "confidence": 0},
                        "report": None,
                        "detail": None,
                    },
                )

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/scenarios")
    @app.get("/scenarios")
    async def list_scenarios():
        return [
            {
                "id": index + 1,
                "name": scenario["name"],
                "description": scenario["description"],
                "expected_decision": scenario.get("expected_decision", ""),
                "transaction": scenario["transaction"].model_dump(),
            }
            for index, scenario in enumerate(DEMO_SCENARIOS)
        ]

    @app.post("/demo/{scenario_number}")
    async def run_demo_scenario(scenario_number: int):
        if scenario_number < 1 or scenario_number > len(DEMO_SCENARIOS):
            raise HTTPException(status_code=400, detail=f"Scenario number must be 1-{len(DEMO_SCENARIOS)}")

        scenario = DEMO_SCENARIOS[scenario_number - 1]
        result = await asyncio.to_thread(orchestrator.process_transaction, scenario["transaction"])
        return {
            "scenario": scenario["name"],
            "description": scenario["description"],
            "transaction_id": scenario["transaction"].transaction_id,
            "decision": result.get("final_decision", "escalate"),
            "message": result.get("final_message", ""),
            "phase1": result.get("phase1_result"),
            "risk_level": result.get("phase1_risk_level"),
            "report": result.get("report"),
            "detail": result.get("decision"),
        }

    return app


app = create_fastapi_app()


def main() -> None:
    if "--serve" in sys.argv:
        import uvicorn

        from configs.settings import settings

        print("\nStarting FastAPI server...")
        print(f"URL: http://{settings.api_host}:{settings.api_port}")
        print(f"Docs: http://localhost:{settings.api_port}/docs")

        uvicorn.run(
            app,
            host=settings.api_host,
            port=settings.api_port,
            log_level="info",
        )
    else:
        run_cli_demo()


if __name__ == "__main__":
    main()
