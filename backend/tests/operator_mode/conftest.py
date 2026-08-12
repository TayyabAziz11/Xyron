import sys
from pathlib import Path

# Add backend/ to sys.path so operator_mode.* imports resolve
_BACKEND = str(Path(__file__).resolve().parent.parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
