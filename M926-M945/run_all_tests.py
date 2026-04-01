#!/usr/bin/env python3
"""Run all M926-M945 tests."""
import subprocess, sys
sys.exit(subprocess.call(["python", "-m", "pytest", "-xvs", "."]))
