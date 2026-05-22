from fastapi.testclient import TestClient


def test_import_main():
    import main  # noqa: F401


def test_dataset_scenarios_are_real_ids():
    from infrastructure.databases.seed_loader import build_demo_scenarios, dataset_summary

    summary = dataset_summary()
    scenarios = build_demo_scenarios()

    assert summary["csv_rows"] == 701
    assert summary["fraud"] == 202
    assert len(scenarios) >= 4
    assert all(not s["transaction"].sender_id.startswith("ACC_") for s in scenarios)
    scenario_pairs = {
        (s["transaction"].sender_id, s["transaction"].receiver_id, s["expected_decision"])
        for s in scenarios
    }
    assert ("C8126703807", "C1409103719", "allow") in scenario_pairs
    assert ("C2972777054", "C8992641070", "block") in scenario_pairs
    assert ("C2006456468", "C3259274595", "block") in scenario_pairs


def test_api_health_login_and_scenarios():
    from services.api.server import create_fastapi_app

    with TestClient(create_fastapi_app()) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["dataset"]["csv_rows"] == 701
        scenarios = client.get("/api/scenarios")
        assert scenarios.status_code == 200
        assert len(scenarios.json()) >= 4

        login = client.post(
            "/api/login",
            json={"username": "C8126703807", "password": "demo"},
        )
        assert login.status_code == 200
        assert login.json()["user"]["id"] == "C8126703807"

        bad_amount = client.post(
            "/api/fraud-detection",
            json={
                "account_id": "C8126703807",
                "recipient_id": "C1409103719",
                "amount": "not-a-number",
            },
        )
        assert bad_amount.status_code == 400


def test_acceptance_decisions_dataset_fallback():
    from core.orchestration.pipeline import FraudDetectionOrchestrator
    from infrastructure.databases.seed_loader import build_demo_scenarios

    expected = {
        "Clean small payment": "allow",
        "Fraud cluster transfer": "block",
        "Second fraud cluster": "block",
        "High-value legitimate transfer": "allow",
    }

    orchestrator = FraudDetectionOrchestrator()
    try:
        orchestrator.initialize()
        for scenario in build_demo_scenarios():
            result = orchestrator.process_transaction(scenario["transaction"])
            assert result.get("final_decision") == expected[scenario["name"]]
    finally:
        orchestrator.shutdown()


def test_low_risk_balance_drain_history_does_not_auto_block():
    from core.orchestration.pipeline import FraudDetectionOrchestrator
    from infrastructure.databases.seed_loader import load_final_csv_rows, transaction_from_row

    row = next(
        row
        for row in load_final_csv_rows()
        if row["sender_account_no"] == "C1411005034"
        and row["receiver_account_no"] == "C1810730392"
    )

    orchestrator = FraudDetectionOrchestrator()
    try:
        orchestrator.initialize()
        result = orchestrator.process_transaction(transaction_from_row(row))
        assert result.get("phase1_risk_level") == "yellow"
        assert result.get("final_decision") != "block"
    finally:
        orchestrator.shutdown()


def test_sse_completes_for_clean_dataset_transaction():
    from services.api.server import create_fastapi_app

    with TestClient(create_fastapi_app()) as client:
        scenario = client.get("/api/scenarios").json()[0]["transaction"]
        payload = {
            "account_id": scenario["sender_id"],
            "recipient_id": scenario["receiver_id"],
            "amount": scenario["amount"],
            "description": scenario["description"],
            "device_id": scenario["device_id"],
            "ip_address": scenario["ip_address"],
            "auth_method": scenario["auth_method"],
            "sender_balance_before": scenario["sender_balance_before"],
            "sender_balance_after": scenario["sender_balance_after"],
        }

        with client.stream("POST", "/api/fraud-detection", json=payload) as response:
            body = "".join(response.iter_text())

        assert response.status_code == 200
        assert "phase1_start" in body
        assert "complete" in body
        assert '"decision": "allow"' in body
