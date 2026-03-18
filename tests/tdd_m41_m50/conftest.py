"""
Shared fixtures for TDD tests M41-M50 (Store and Tracer growth memory).

TEST-DRIVEN DEVELOPMENT: No mock implementations. All fixtures provide
real data structures that the implementation code must handle.
"""

import os
import sys
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Growth stage constants used across all tests
GROWTH_STAGES = {
    0: "infant",
    1: "toddler",
    2: "elementary",
    3: "middle",
    4: "high",
    5: "college",
    6: "graduate",
}

VALID_MATURITY_LEVELS = list(range(7))  # 0-6
