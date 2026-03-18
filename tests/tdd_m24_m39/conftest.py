"""
Shared fixtures for TDD M24-M39 test suite.

This is TEST-DRIVEN DEVELOPMENT: tests are written FIRST against expected
input/output contracts. No mock implementations. We test the REAL code.
"""

import sys
import os
import pytest

# Ensure project root is on path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
