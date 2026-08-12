"""Central tool registry — import and register all tools at module load time."""
from __future__ import annotations

from .registry import registry, ToolResult  # noqa: F401

# Register all tool modules (side-effect: populates registry)
from . import system_tools       # noqa: F401
from . import web_tools          # noqa: F401
from . import content_tools      # noqa: F401
from . import screen_tools       # noqa: F401
from . import gmail_tools        # noqa: F401
from . import calendar_tools     # noqa: F401
from . import browser_tools      # noqa: F401  — browser_navigate/click/fill/read/screenshot
from . import automation_tools   # noqa: F401  — desktop_type/hotkey/click/scroll + run_workflow
from . import core_tools         # noqa: F401  — drives, file_search, media_control, app_finder
from . import store_tools        # noqa: F401  — install_store_app, install_store_app_exec

__all__ = ["registry", "ToolResult"]
