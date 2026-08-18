#!/usr/bin/env python3
"""Testlauf ohne pytest -- fuer Umgebungen, in denen pytest nicht vorhanden ist.

    python3 api/tests/run_tests.py

Mit pytest geht auch:  pytest api/tests
"""
import importlib
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MODULES = ["test_pdf_parser", "test_distribution", "test_xlsx_export"]


def main() -> int:
    passed = failed = skipped = 0
    for mod_name in MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError as e:
            print(f"\n[uebersprungen] {mod_name}: {e}")
            skipped += 1
            continue
        print(f"\n{mod_name}")
        for name in sorted(n for n in dir(mod) if n.startswith("test_")):
            try:
                getattr(mod, name)()
                print(f"  PASS  {name}")
                passed += 1
            except Exception:
                print(f"  FAIL  {name}")
                traceback.print_exc(limit=3)
                failed += 1

    print(f"\n{passed} bestanden, {failed} fehlgeschlagen"
          + (f", {skipped} Modul(e) uebersprungen" if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
