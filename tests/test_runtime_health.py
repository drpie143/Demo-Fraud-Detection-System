from types import SimpleNamespace

import pytest

from infrastructure.health import runtime_health


def test_failed_cloud_databases_are_not_reported_as_real_services():
    services = {"redis": "simulator", "neo4j": "simulator", "mongodb": "simulator", "chromadb": "connected"}
    payload = runtime_health(False, services)
    assert payload["status"] == "degraded"
    assert payload["mode"] == "hybrid_fallback"
    assert payload["unavailable_services"] == ["redis", "neo4j", "mongodb"]


def test_offline_demo_is_explicit_and_all_cloud_connections_are_required():
    offline = {name: "simulator" for name in ("redis", "neo4j", "mongodb", "chromadb")}
    assert runtime_health(True, offline)["mode"] == "demo_simulator"
    assert runtime_health(False, offline)["mode"] == "simulator_fallback"
    connected = dict.fromkeys(offline, "connected")
    assert runtime_health(False, connected)["mode"] == "real_services"


def test_gemini_clients_do_not_share_api_key_configuration(monkeypatch):
    from infrastructure.llm import gemini

    clients = []

    def client_factory(**kwargs):
        key = kwargs["api_key"]
        client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **call: SimpleNamespace(text=key)))
        clients.append(client)
        return client

    monkeypatch.setattr(gemini.settings, "demo_mode", False)
    monkeypatch.setattr(gemini.genai, "Client", client_factory)
    first, second = gemini.GeminiProvider("first-key"), gemini.GeminiProvider("second-key")
    assert first.generate("one") == "first-key"
    assert second.generate("two") == "second-key"
    assert first.generate("three") == "first-key"
    assert len(clients) == 2


def test_strict_benchmark_rejects_missing_gemini(monkeypatch):
    from infrastructure.llm.gemini import GeminiProvider

    monkeypatch.setenv("GEMINI_STRICT_ERRORS", "true")
    with pytest.raises(RuntimeError, match="configured Gemini API key"):
        GeminiProvider().generate("test")
