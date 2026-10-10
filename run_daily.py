"""Entry point for the daily job: python run_daily.py [--date YYYY-MM-DD] [--ipo ID_OR_SYMBOL] [--dry-run]"""
from __future__ import annotations

import sys

from ipo_agents.cli import main

if __name__ == "__main__":
    sys.exit(main())
