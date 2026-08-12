"""Root conftest — makes backend/ importable for all tests."""
import sys
from pathlib import Path

_BACKEND = str(Path(__file__).resolve().parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
