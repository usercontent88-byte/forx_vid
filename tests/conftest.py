"""Ensure the repository root is importable under all pytest launchers."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
ROOT_TEXT = str(ROOT)
if ROOT_TEXT not in sys.path:
    sys.path.insert(0, ROOT_TEXT)
