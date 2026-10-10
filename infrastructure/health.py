"""Describe configured runtime and actual database connectivity separately."""

DATABASE_SERVICES = ("redis", "neo4j", "mongodb", "chromadb")


def runtime_health(demo_mode: bool, services: dict[str, str]) -> dict:
    unavailable = [name for name in DATABASE_SERVICES if services.get(name) != "connected"]
    if demo_mode:
        return {"status": "healthy", "mode": "demo_simulator", "unavailable_services": []}
    if unavailable:
        connected = len(unavailable) < len(DATABASE_SERVICES)
        return {
            "status": "degraded",
            "mode": "hybrid_fallback" if connected else "simulator_fallback",
            "unavailable_services": unavailable,
        }
    return {"status": "healthy", "mode": "real_services", "unavailable_services": []}
