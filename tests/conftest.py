import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure():
    os.environ["DEMO_MODE"] = "true"
    os.environ["REDIS_HOST"] = "localhost"
    for key in [
        "GEMINI_API_KEY",
        "GEMINI_API_KEY_PLANNER",
        "GEMINI_API_KEY_EXECUTOR",
        "GEMINI_API_KEY_EXECUTOR_POOL",
        "GEMINI_API_KEY_DETECTIVE",
        "GEMINI_API_KEY_VISION",
        "GEMINI_API_KEY_REPORT",
        "NEO4J_URI",
        "NEO4J_PASSWORD",
        "MONGODB_URI",
        "CHROMA_API_KEY",
        "REDIS_PASSWORD",
    ]:
        os.environ[key] = ""
