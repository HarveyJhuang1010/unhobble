"""Self-test for the bench. Run before trusting any number it prints:

    python3 bench/test_bench.py

Nothing here calls `claude` or spends API money. The quality checks run real
node against fixtures written in the tests themselves, because a check that
cannot tell a correct fix from a wrong one makes every summary meaningless.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH))

MODULES = ("test_checks", "test_quality", "test_summary", "test_pipeline", "test_safety")


def load_tests(loader: unittest.TestLoader, tests, pattern) -> unittest.TestSuite:
    return unittest.TestSuite(loader.loadTestsFromName(name) for name in MODULES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
