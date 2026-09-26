#!/usr/bin/env python3
"""Repository-local entry point used by the QML plugin."""

from __future__ import annotations

import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from omajump.backend import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
