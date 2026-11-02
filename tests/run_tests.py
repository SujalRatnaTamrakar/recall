#!/usr/bin/env python3
"""Plain test runner (no pytest required):  python tests/run_tests.py

Discovers every test_* function in tests/test_*.py, runs it, and prints a
pass/fail summary.  Exit code 0 iff all pass.  The same test modules also
run under pytest if it is installed (python -m pytest tests/).
"""
from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    passed, failed = 0, []
    for path in sorted(HERE.glob("test_*.py")):
        mod = load_module(path)
        for name in sorted(dir(mod)):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            try:
                fn()
                passed += 1
                print(f"PASS {path.name}::{name}")
            except Exception:
                failed.append(f"{path.name}::{name}")
                print(f"FAIL {path.name}::{name}")
                traceback.print_exc()
    print(f"\n{passed} passed, {len(failed)} failed")
    if failed:
        print("failed:", ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
