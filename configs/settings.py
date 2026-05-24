"""Runtime settings for the dataset-backed fraud detection system."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from pydantic import BaseModel


# Windows consoles often default to cp1252, which can crash on Vietnamese text
# and status symbols emitted during startup.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

load_dotenv()


class Settings(BaseModel):
    """Global configuration for the fraud detection runtime."""

    # DEMO_MODE=false is the real-service path. Tests explicitly override this
    # to true so they stay offline and deterministic.
    demo_mode: bool = False

    # Gemini
    gemini_api_key: str = ""
    gemini_model_id: str = "gemini-2.5-flash"
    gemini_api_key_planner: str = ""
    gemini_api_key_executor: str = ""
    gemini_api_key_executor_pool: str = ""
    gemini_api_key_detective: str = ""
    gemini_api_key_vision: str = ""
    gemini_api_key_report: str = ""

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_allow_self_signed_fallback: bool = True

    # ChromaDB
    chroma_host: str = "api.trychroma.com"
    chroma_api_key: str = ""
    chroma_tenant: str = ""
    chroma_database: str = ""
    chroma_collection_name: str = "fraud_knowledge_base"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_username: str = "default"
    redis_password: str = ""

    # MongoDB
    mongodb_uri: str = ""
    mongodb_db_name: str = "fraud_detection"

    # Internal simulator table names.
    dynamodb_endpoint: str = "http://localhost:8000"
    dynamodb_table_transactions: str = "fraud_transactions"
    dynamodb_table_profiles: str = "customer_profiles"

    # FastAPI. Hosted platforms such as Render/Railway inject PORT, so PORT
    # takes precedence over local API_PORT.
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Agent configuration
    auto_seed_on_startup: bool = False
    max_investigation_steps: int = 10
    investigation_timeout: int = 30
    confidence_threshold: float = 0.85

    # Dataset-scale screening rules
    instant_allow_max: float = 5000.0
    elevated_amount_threshold: float = 5000.0
    large_amount_threshold: float = 1000000.0
    suspicious_velocity_threshold: int = 5
    high_risk_threshold: float = 0.6
    red_risk_threshold: float = 0.9


def get_settings() -> Settings:
    """Build a Settings object from environment variables."""

    return Settings(
        demo_mode=os.getenv("DEMO_MODE", "true").lower() == "true",
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model_id=os.getenv("GEMINI_MODEL_ID", "gemini-2.5-flash"),
        gemini_api_key_planner=os.getenv("GEMINI_API_KEY_PLANNER", ""),
        gemini_api_key_executor=os.getenv("GEMINI_API_KEY_EXECUTOR", ""),
        gemini_api_key_executor_pool=os.getenv("GEMINI_API_KEY_EXECUTOR_POOL", ""),
        gemini_api_key_detective=os.getenv("GEMINI_API_KEY_DETECTIVE", ""),
        gemini_api_key_vision=os.getenv("GEMINI_API_KEY_VISION", ""),
        gemini_api_key_report=os.getenv("GEMINI_API_KEY_REPORT", ""),
        neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
        neo4j_password=os.getenv("NEO4J_PASSWORD", ""),
        neo4j_allow_self_signed_fallback=os.getenv(
            "NEO4J_ALLOW_SELF_SIGNED_FALLBACK", "true"
        ).lower() == "true",
        chroma_host=os.getenv("CHROMA_HOST", "api.trychroma.com"),
        chroma_api_key=os.getenv("CHROMA_API_KEY", ""),
        chroma_tenant=os.getenv("CHROMA_TENANT", ""),
        chroma_database=os.getenv("CHROMA_DATABASE", ""),
        chroma_collection_name=os.getenv("CHROMA_COLLECTION_NAME", "fraud_knowledge_base"),
        mongodb_uri=os.getenv("MONGODB_URI", ""),
        mongodb_db_name=os.getenv("MONGODB_DB_NAME", "fraud_detection"),
        redis_host=os.getenv("REDIS_HOST", "localhost"),
        redis_port=int(os.getenv("REDIS_PORT", "6379")),
        redis_username=os.getenv("REDIS_USERNAME", "default"),
        redis_password=os.getenv("REDIS_PASSWORD", ""),
        dynamodb_endpoint=os.getenv("DYNAMODB_ENDPOINT", "http://localhost:8000"),
        dynamodb_table_transactions=os.getenv("DYNAMODB_TABLE_TRANSACTIONS", "fraud_transactions"),
        dynamodb_table_profiles=os.getenv("DYNAMODB_TABLE_PROFILES", "customer_profiles"),
        api_host=os.getenv("API_HOST", "0.0.0.0"),
        api_port=int(os.getenv("PORT") or os.getenv("API_PORT", "8000")),
        auto_seed_on_startup=os.getenv("AUTO_SEED_ON_STARTUP", "false").lower() == "true",
        max_investigation_steps=int(os.getenv("MAX_INVESTIGATION_STEPS", "10")),
        investigation_timeout=int(os.getenv("INVESTIGATION_TIMEOUT", "30")),
        confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.85")),
        instant_allow_max=float(os.getenv("INSTANT_ALLOW_MAX", "5000")),
        elevated_amount_threshold=float(os.getenv("ELEVATED_AMOUNT_THRESHOLD", "5000")),
        large_amount_threshold=float(os.getenv("LARGE_AMOUNT_THRESHOLD", "1000000")),
        suspicious_velocity_threshold=int(os.getenv("SUSPICIOUS_VELOCITY_THRESHOLD", "5")),
        high_risk_threshold=float(os.getenv("HIGH_RISK_THRESHOLD", "0.6")),
        red_risk_threshold=float(os.getenv("RED_RISK_THRESHOLD", "0.9")),
    )


settings = get_settings()
