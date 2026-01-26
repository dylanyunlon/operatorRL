"""
Tests for Agent OS unified package.

Run with: pytest tests/ -v
"""

import sys
from pathlib import Path

# Add packages to path for testing
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "primitives"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "control-plane" / "src"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "iatp"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "cmvk" / "src"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "caas" / "src"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "emk"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "amb"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "atr"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "scak"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "mute-agent" / "src"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "mute-agent"))
