#!/usr/bin/env python3
"""
Test runner for Context-as-a-Service

Runs all tests and reports results in a clear format.
"""

import sys
import subprocess
from pathlib import Path
from typing import List, Tuple

# Test files to run
TEST_FILES = [
    "tests/test_functionality.py",
    "tests/test_structure_aware_indexing.py",
    "tests/test_metadata_injection.py",
    "tests/test_time_decay.py",
    "tests/test_context_triad.py",
    "tests/test_pragmatic_truth.py",
    "tests/test_heuristic_router.py",
    "tests/test_conversation_manager.py",
    "tests/test_trust_gateway.py",
]


def run_test(test_file: str) -> Tuple[bool, str]:
    """
    Run a single test file.
    
    Args:
        test_file: Path to test file
        
    Returns:
        Tuple of (success, output)
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", test_file.replace("/", ".").replace(".py", "")],
            capture_output=True,
            text=True,
            timeout=60
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Test timed out after 60 seconds"
    except Exception as e:
        return False, f"Error running test: {e}"


def main():
    """Run all tests and report results."""
    print("=" * 70)
    print("Context-as-a-Service Test Suite")
    print("=" * 70)
    print()
    
    results = []
    passed = 0
    failed = 0
    
    for test_file in TEST_FILES:
        test_name = Path(test_file).stem
        print(f"Running {test_name}...", end=" ", flush=True)
        
        success, output = run_test(test_file)
        results.append((test_name, success, output))
        
        if success:
            print("✅ PASSED")
            passed += 1
        else:
            print("❌ FAILED")
            failed += 1
    
    # Print summary
    print()
    print("=" * 70)
    print("Test Summary")
    print("=" * 70)
    print(f"Total tests: {len(TEST_FILES)}")
    print(f"Passed: {passed} ✅")
    print(f"Failed: {failed} ❌")
    print()
    
    # Print failed test details
    if failed > 0:
        print("=" * 70)
        print("Failed Test Details")
        print("=" * 70)
        for test_name, success, output in results:
            if not success:
                print(f"\n{test_name}:")
                print("-" * 70)
                # Print last 50 lines of output
                lines = output.split("\n")
                print("\n".join(lines[-50:]))
    
    # Exit with appropriate code
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
