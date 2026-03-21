"""Pytest bootstrap for stable local/CI package imports."""

from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT_TEXT = str(REPO_ROOT)

if REPO_ROOT_TEXT not in sys.path:
    sys.path.insert(0, REPO_ROOT_TEXT)
