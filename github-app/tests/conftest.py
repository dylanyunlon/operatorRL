"""Ensure the github-app directory is importable."""
import sys
from pathlib import Path

# Add github-app/ to sys.path so `from app import ...` works
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
