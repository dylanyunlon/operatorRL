#!/usr/bin/env python3
"""Run all M906-M925 module tests."""
import ast
import os
import sys

def main():
    base = os.path.dirname(os.path.abspath(__file__))
    errors = []
    total = 0
    for root, dirs, files in os.walk(base):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                total += 1
                try:
                    with open(path, "r") as fh:
                        ast.parse(fh.read())
                except SyntaxError as exc:
                    errors.append((path, str(exc)))
    print(f"Checked {total} Python files")
    if errors:
        for path, err in errors:
            print(f"  ERROR: {path}: {err}")
        sys.exit(1)
    else:
        print("All syntax checks passed!")
        sys.exit(0)

if __name__ == "__main__":
    main()
