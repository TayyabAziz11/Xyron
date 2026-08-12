"""Phase 4 Coordinator Agent package."""
from __future__ import annotations

# Import the submodule so it becomes an attribute of this package.
# AgentRuntime resolves AgentType.COORDINATOR by:
#   mod = importlib.import_module("api.agents.coordinator")   # this package
#   specialist = getattr(mod, "coordinator_agent")            # the submodule below
#   await specialist.run(task, runtime, cancel_event, pause_event)
from api.agents.coordinator import coordinator_agent  # noqa: F401

__all__ = ["coordinator_agent"]
