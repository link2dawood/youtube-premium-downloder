"""Make `native/` importable as a top-level package for tests."""
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
NATIVE_DIR = os.path.join(REPO_ROOT, "native")

for path in (REPO_ROOT, NATIVE_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)
