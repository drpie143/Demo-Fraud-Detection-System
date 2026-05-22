#!/usr/bin/env python3
# ====================================================================
# MAIN.PY - Entry Point for Fraud Detection System
# ====================================================================
#
# Usage:
#   python main.py          # CLI Demo - run 3 scenarios
#   python main.py --serve  # API Server on http://localhost:8000
#
# ====================================================================

import sys
import os

# Add project root to path
_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Import and run from services.api.server
from services.api.server import main

if __name__ == "__main__":
    main()
