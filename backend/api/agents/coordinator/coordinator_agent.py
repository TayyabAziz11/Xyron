"""
CoordinatorAgent — Phase 4 multi-agent workflow orchestrator.

Entry point called by AgentRuntime when AgentType.COORDINATOR is launched.
Delegates to specialist agents via TaskGraph execution.
Never blocks the voice pipeline — runs as a background asyncio.Task.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Awaitable, Callable, Optional

_BOOKING_GOAL_RE = re.compile(
    r"\b(flight|flights|book|booking|ticket|tickets|hotel|purchase|buy|checkout)\b",
    re.IGNORECASE,
)

logger = logging.getLogger("api.agents.coordinator")


async def run(task: Any, runtime: Any, cancel_event: asyncio.Event, pause_event: asyncio.Event) -> str:
    """Entry point called by AgentRuntime for COORDINATOR agent type."""
    from api.agents.coordinator.delegation_planner import delegation_planner
    from api.agents.coordinator.task_graph import TaskGraph, NodeStatus
    from api.agents.coordinator.collaboration_memory import collaboration_memory
    from api.agents.coordinator.progress_reporter import progress_reporter
    from api.agents.coordinator.reflection_engine import reflection_engine
    from api.agents.coordinator.coordinator_verifier import coordinator_verifier
    from api.agents.coordinator.resource_locks import resource_lock_manager
    from api.agents.agent_types import AgentType, AgentStatus
    from api.agents.agent_runtime import agent_runtime

    goal = task.goal
    ws_send = task.ws_send_fn
    workflow_start = time.time()

    logger.info("[COORDINATOR_START] goal=%r task_id=%s", goal[:80], task.task_id)

    async def send(payload: dict) -> None:
        if ws_send:
            try:
                await ws_send(payload)
            except Exception:
                pass

    _last_progress: dict[str, Any] = {"key": None}

    async def send_progress(message: str, pct: int = 0, graph: Optional[TaskGraph] = None) -> None:
        # Phase 4.13: the sub-agent poll loop below called this every 1s
        # regardless of whether anything had actually changed, flooding
        # the WS with identical "Research: Step 0/1: running"/
        # "waiting_approval" events — pure noise, nothing new to show.
        # Only send when the (message, pct) pair actually differs from
        # the last one sent for this task.
        key = (message, pct)
        if key == _last_progress["key"]:
            logger.debug("[PROGRESS_UPDATE_SKIPPED_DUPLICATE] task=%s pct=%d message=%r",
                         task.task_id, pct, message[:80])
            return
        _last_progress["key"] = key
        logger.info("[PROGRESS_UPDATE_SENT] task=%s pct=%d message=%r", task.task_id, pct, message[:80])
        payload: dict[str, Any] = {
            "type":    "coordinator_progress",
            "task_id": task.task_id,
            "message": message,
            "pct":     pct,
            "status":  task.status.value if hasattr(task.status, "value") else str(task.status),
        }
        if graph:
            payload["workflow"] = graph.to_dict()
        await send(payload)

    # Import personality for narration
    try:
        from api.agents.personality.personality_engine import personality_engine as _pe
    except Exception:
        _pe = None

    def _narrate(step: str, ctx: dict | None = None) -> str:
        if _pe:
            return _pe.narrate_step(step, ctx or {})
        return step

    # Phase 4.15: browser/travel tasks get their own, faster, jargon-free
    # narration from conversation_layer.py the moment browser_agent.py
    # starts (e.g. "Let me check." / "I'm opening Google Flights."). The
    # generic "Let me plan out how to handle your request." / "I've
    # planned N steps." lines fired here BEFORE that — live-measured
    # adding two extra full TTS synthesis+playback cycles of pure
    # workflow-logger-sounding chatter ahead of the first useful thing the
    # user actually hears. Skip them for browser tasks; other agent types
    # (coding/automation) don't have an equivalent, so they still narrate.
    _primary_type = task.metadata.get("context", {}).get("primary_type")
    _is_browser_task = _primary_type == "browser"

    # ── Step 1: Plan ──────────────────────────────────────────────────────────
    if not _is_browser_task:
        await send_progress(_narrate("coordinator.planning", {"goal": goal[:60]}), 5)

    try:
        graph, delegation_type = await delegation_planner.plan(
            goal, task.metadata.get("context", {})
        )
    except Exception as plan_exc:
        logger.error("[COORDINATOR_FAIL] planning error: %s", plan_exc)
        err_resp = "I couldn't plan that task. Please try again."
        await send({"type": "coordinator_complete", "task_id": task.task_id,
                    "summary": err_resp, "verified": False})
        return err_resp

    logger.info("[COORDINATOR_GOAL_ANALYZED] delegation=%s nodes=%d goal=%r",
                delegation_type, len(graph.nodes), goal[:60])
    logger.info("[COORDINATOR_TASK_GRAPH_CREATED] workflow_id=%s nodes=%d",
                graph.workflow_id, len(graph.nodes))
    if not _is_browser_task:
        await send_progress(f"I've planned {len(graph.nodes)} steps. Starting now.", 10, graph)

    # ── Step 2: Execute task graph ────────────────────────────────────────────
    final_output: dict[str, Any] = {}

    try:
        final_output = await _execute_graph(
            graph=graph,
            task=task,
            runtime=runtime,
            cancel_event=cancel_event,
            pause_event=pause_event,
            send_progress=send_progress,
            send=send,
            resource_lock_manager=resource_lock_manager,
        )
    except asyncio.CancelledError:
        logger.info("[COORDINATOR_CANCELLED] task_id=%s", task.task_id)
        cancel_resp = progress_reporter.format_cancellation(graph)
        await send({"type": "coordinator_cancelled", "task_id": task.task_id, "summary": cancel_resp})
        return cancel_resp
    except Exception as exec_exc:
        logger.exception("[COORDINATOR_FAIL] execution error: %s", exec_exc)
        final_output["error"] = str(exec_exc)

    # ── Step 3: Verify ────────────────────────────────────────────────────────
    verified = False
    try:
        verified = await coordinator_verifier.verify(graph, final_output)
        logger.info("[COORDINATOR_VERIFY] verified=%s", verified)
    except Exception as ve:
        logger.warning("[COORDINATOR_VERIFY] error: %s", ve)

    # ── Step 4: Final response ────────────────────────────────────────────────
    duration_s = round(time.time() - workflow_start, 1)
    raw_summary = _build_raw_summary(graph, final_output, duration_s, verified)

    final_response = raw_summary
    try:
        from api.agents.personality.personality_engine import personality_engine
        final_response = personality_engine.polish_response(raw_summary, context={"workflow": True})
        logger.info("[PERSONALITY_FINAL_RESPONSE] mode=%s raw=%r polished=%r",
                    personality_engine.mode.value, raw_summary[:60], final_response[:60])
        logger.info("[PERSONALITY_MODE_APPLIED] mode=%s", personality_engine.mode.value)
    except Exception as pe:
        logger.debug("[COORDINATOR] personality polish skipped: %s", pe)

    logger.info("[COORDINATOR_COMPLETE] task_id=%s duration_s=%.1f verified=%s response=%r",
                task.task_id, duration_s, verified, final_response[:80])
    logger.info("[COORDINATOR_END] task_id=%s ms=%.0f", task.task_id, duration_s * 1000)

    # ── Step 5: Reflect and save memory ──────────────────────────────────────
    try:
        await reflection_engine.reflect(graph, final_output, duration_s, verified)
        collaboration_memory.write(
            key=f"workflow_{graph.workflow_id}",
            value={
                "goal":       goal,
                "delegation": delegation_type,
                "nodes":      len(graph.nodes),
                "verified":   verified,
                "duration_s": duration_s,
                "summary":    final_response,
                "outputs":    {k: str(v)[:200] for k, v in final_output.items()},
            },
            persist=True,
        )
    except Exception as me:
        logger.debug("[COORDINATOR] memory/reflect error: %s", me)

    # Notify frontend
    await send({
        "type":       "coordinator_complete",
        "task_id":    task.task_id,
        "summary":    final_response,
        "verified":   verified,
        "duration_s": duration_s,
        "workflow":   graph.to_dict(),
    })

    return final_response


# ── Graph execution engine ────────────────────────────────────────────────────

async def _execute_graph(
    graph: Any,
    task: Any,
    runtime: Any,
    cancel_event: asyncio.Event,
    pause_event: asyncio.Event,
    send_progress: Callable,
    send: Callable,
    resource_lock_manager: Any,
) -> dict[str, Any]:
    """
    Execute the TaskGraph node by node, respecting dependencies.
    Parallel-safe: nodes with can_run_parallel=True run concurrently.
    """
    from api.agents.coordinator.task_graph import NodeStatus
    from api.agents.agent_types import AgentStatus
    from api.agents.agent_runtime import agent_runtime as ar

    accumulated: dict[str, Any] = {}
    max_iterations = len(graph.nodes) * 4
    iteration = 0

    while not graph.is_complete() and not graph.is_failed() and iteration < max_iterations:
        iteration += 1

        # Cancel check
        if cancel_event.is_set():
            logger.info("[COORDINATOR_CANCELLED] during graph iteration=%d", iteration)
            for node in graph.nodes.values():
                if node.status == NodeStatus.RUNNING:
                    graph.mark_failed(node.node_id, "Coordinator cancelled")
            raise asyncio.CancelledError()

        # Pause support
        if not pause_event.is_set():
            logger.info("[WORKFLOW_PAUSE] paused during graph execution")
            await send({"type": "coordinator_paused", "task_id": task.task_id})
            await pause_event.wait()
            logger.info("[WORKFLOW_RESUME] resumed")
            await send({"type": "coordinator_resumed", "task_id": task.task_id})

        ready = graph.get_ready_nodes()
        if not ready:
            running = [n for n in graph.nodes.values() if n.status == NodeStatus.RUNNING]
            if not running:
                break
            await asyncio.sleep(0.5)
            continue

        # Split into parallel and sequential batches
        parallel = [n for n in ready if n.can_run_parallel]
        sequential = [n for n in ready if not n.can_run_parallel]

        # Run parallel batch
        if len(parallel) > 1:
            logger.info("[TASK_GRAPH_PARALLEL_START] nodes=%d", len(parallel))
            coros = [
                _execute_node(n, graph, task, ar, accumulated, send_progress, send, resource_lock_manager)
                for n in parallel
            ]
            results = await asyncio.gather(*coros, return_exceptions=True)
            for node, result in zip(parallel, results):
                if isinstance(result, Exception):
                    logger.error("[COORDINATOR_NODE_ERROR] parallel node=%s err=%s", node.node_id, result)
                    graph.mark_failed(node.node_id, str(result))
                elif isinstance(result, dict):
                    accumulated.update(result)
        elif parallel:
            # Only one parallel node — just run it
            node = parallel[0]
            result = await _execute_node(
                node, graph, task, ar, accumulated, send_progress, send, resource_lock_manager
            )
            accumulated.update(result or {})

        # Run sequential nodes one at a time
        for node in sequential:
            node.input_data.update(accumulated)
            result = await _execute_node(
                node, graph, task, ar, accumulated, send_progress, send, resource_lock_manager
            )
            accumulated.update(result or {})

            pct = graph.get_progress_pct()
            # Narrate completion of this step and hint at next
            done_nodes = [n for n in graph.nodes.values() if n.status.value == "done"]
            remaining  = [n for n in graph.nodes.values() if n.status.value in ("pending", "running")]
            if remaining:
                next_title = remaining[0].title
                step_msg = f"I've finished: {node.title}. Now starting: {next_title}."
            else:
                step_msg = f"I've finished: {node.title}."
            await send_progress(step_msg, pct, graph)

            # Cancel check between sequential steps
            if cancel_event.is_set():
                raise asyncio.CancelledError()

        await asyncio.sleep(0.05)

    return accumulated


async def _execute_node(
    node: Any,
    graph: Any,
    coord_task: Any,
    agent_runtime: Any,
    context: dict,
    send_progress: Callable,
    send: Callable,
    resource_lock_manager: Any,
) -> dict:
    """Execute a single TaskNode — routes to agent, verifier, or personality."""
    from api.agents.coordinator.task_graph import NodeStatus

    graph.mark_started(node.node_id, task_id="pending")
    logger.info("[COORDINATOR_DELEGATE] node=%s agent=%s goal=%r",
                node.node_id, node.agent_type, node.goal[:60])
    # Announce delegation to user
    try:
        from api.agents.personality.personality_engine import personality_engine as _cpe
        delegate_msg = _cpe.narrate_step(
            "coordinator.delegating",
            {"agent": node.agent_type, "node_id": node.node_id, "goal": node.goal[:40]},
        )
    except Exception:
        delegate_msg = f"Now running: {node.title}"
    graph.nodes[node.node_id]._narration = delegate_msg  # stored for UI

    # Approval gate
    if node.requires_approval:
        graph.nodes[node.node_id].status = NodeStatus.APPROVAL_REQUIRED
        await send({
            "type":        "coordinator_approval_required",
            "task_id":     coord_task.task_id,
            "node_id":     node.node_id,
            "action":      node.title,
            "description": node.goal,
        })
        logger.info("[COORDINATOR_APPROVAL_REQUIRED] node=%s action=%r", node.node_id, node.title)
        for _ in range(240):  # wait up to 120 s
            await asyncio.sleep(0.5)
            if coord_task.metadata.get(f"approved_{node.node_id}") is True:
                logger.info("[COORDINATOR_APPROVAL_ACCEPTED] node=%s", node.node_id)
                graph.nodes[node.node_id].status = NodeStatus.RUNNING
                break
            if coord_task.metadata.get(f"approved_{node.node_id}") is False:
                logger.info("[COORDINATOR_APPROVAL_REJECTED] node=%s", node.node_id)
                logger.info("[SAFETY_BLOCKED_ACTION] node=%s action=%r", node.node_id, node.title)
                graph.mark_skipped(node.node_id)
                return {}
        else:
            graph.mark_skipped(node.node_id)
            return {}

    try:
        if node.agent_type == "verifier":
            return await _run_verifier_node(node, graph, context)
        elif node.agent_type == "personality":
            return await _run_personality_node(node, graph, context)
        else:
            return await _run_agent_node(
                node, graph, coord_task, agent_runtime, context,
                send_progress, send, resource_lock_manager,
            )
    except Exception as exc:
        logger.error("[COORDINATOR_NODE_ERROR] node=%s err=%s", node.node_id, exc)
        if node.retries < node.max_retries:
            node.retries += 1
            logger.info("[COORDINATOR_RECOVERY] retry node=%s attempt=%d", node.node_id, node.retries)
            graph.nodes[node.node_id].status = NodeStatus.PENDING
            return {}
        graph.mark_failed(node.node_id, str(exc))
        return {}


async def _run_agent_node(
    node: Any,
    graph: Any,
    coord_task: Any,
    agent_runtime: Any,
    context: dict,
    send_progress: Callable,
    send: Callable,
    resource_lock_manager: Any,
) -> dict:
    """Launch a browser/coding/automation agent and wait for completion."""
    from api.agents.agent_types import AgentType, AgentStatus

    _type_map = {
        "browser":    AgentType.BROWSER,
        "coding":     AgentType.CODING,
        "automation": AgentType.AUTOMATION,
    }
    atype = _type_map.get(node.agent_type, AgentType.GENERIC)

    # Enrich goal with context from prior nodes
    enriched_goal = node.goal
    if context.get("research_summary"):
        enriched_goal += f"\n\nPrior research: {context['research_summary'][:400]}"
    if context.get("project_path"):
        enriched_goal += f"\n\nProject path: {context['project_path']}"

    # Phase 3 observation loop — snapshot World State before dispatch so a
    # declared success_condition (if any) can be checked against what
    # actually changed, not just whether the sub-agent claims success.
    world_state_before: dict = {}
    if node.success_condition:
        try:
            from api.services.world_state import world_state
            world_state_before = world_state.get_context()
        except Exception:
            pass

    lock_name = node.agent_type
    async with resource_lock_manager.lock(lock_name):
        sub_task = await agent_runtime.launch(
            goal=enriched_goal,
            agent_type=atype,
            ws_send_fn=coord_task.ws_send_fn,
            context={
                **context,
                "coordinator_task_id": coord_task.task_id,
                "node_id": node.node_id,
            },
        )
        graph.mark_started(node.node_id, task_id=sub_task.task_id)
        logger.info("[COORDINATOR_AGENT_RESULT] waiting node=%s sub_task=%s",
                    node.node_id, sub_task.task_id)

        # Poll for completion (max 5 min)
        waited = 0.0
        while waited < 300:
            await asyncio.sleep(1.0)
            waited += 1.0
            current = agent_runtime.get_task(sub_task.task_id)
            if current is None:
                break
            if current.status in (AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.CANCELLED):
                break
            await send_progress(
                f"{node.title}: {current.progress_text()}",
                graph.get_progress_pct(),
                graph,
            )

        final_task = agent_runtime.get_task(sub_task.task_id)
        if final_task and final_task.status == AgentStatus.COMPLETED:
            # Phase 3 observation loop — the sub-agent says it succeeded;
            # if this node declared a success_condition, confirm World
            # State actually reflects it before trusting that.
            if node.success_condition:
                try:
                    from api.services.world_state import world_state
                    from api.agents.world_state_check import check_condition
                    world_state_after = world_state.get_context(refresh=True)
                    condition_ok, reason = check_condition(
                        node.success_condition, world_state_before, world_state_after
                    )
                except Exception:
                    logger.debug("[COORDINATOR] world state condition check failed", exc_info=True)
                    condition_ok, reason = True, "check errored — not blocking"
                if not condition_ok:
                    logger.warning("[COORDINATOR_NODE_CONDITION_FAILED] node=%s reason=%s",
                                    node.node_id, reason)
                    return await _handle_node_condition_failure(node, graph, reason, agent_runtime, coord_task)

            result: dict[str, Any] = {"result_summary": final_task.result_summary or ""}
            if node.agent_type == "browser" and final_task.result_summary:
                result["research_summary"] = final_task.result_summary
            elif node.agent_type == "coding":
                result["project_path"]  = final_task.metadata.get("project_path", "")
                result["preview_url"]   = final_task.metadata.get("preview_url", "")
            graph.mark_done(node.node_id, result)
            logger.info("[COORDINATOR_AGENT_RESULT] node=%s DONE", node.node_id)

            # Research/search results are never gated, but continuing on to an
            # actual booking/purchase step always is. No booking step exists
            # in this graph today, so this is an informational notice rather
            # than a blocking wait — it documents the safety boundary without
            # delaying the spoken summary of results already found.
            if node.agent_type == "browser" and _BOOKING_GOAL_RE.search(node.goal):
                logger.info(
                    "[COORDINATOR_APPROVAL_REQUIRED] node=%s action=continue_to_booking",
                    node.node_id,
                )
                await send({
                    "type": "coordinator_approval_required",
                    "task_id": coord_task.task_id,
                    "node_id": node.node_id,
                    "action": "continue_to_booking",
                    "description": "Before I continue to booking, I need your confirmation.",
                })

            return result
        else:
            err = getattr(final_task, "error_message", None) or "timeout or unknown failure"
            graph.mark_failed(node.node_id, err)
            return {}


async def _handle_node_condition_failure(
    node: Any, graph: Any, reason: str, agent_runtime: Any, coord_task: Any,
) -> dict:
    """
    A sub-agent reported success but the node's declared success_condition
    didn't hold against World State. Mirrors _execute_node's existing
    exception-based retry (same retries < max_retries check), and — new in
    Phase 3 — dispatches a rollback goal once retries are exhausted, if the
    node declared one. Rollback is fire-and-forget (not awaited to
    completion) so a slow cleanup task never blocks the rest of the
    workflow or the user-facing summary.
    """
    from api.agents.coordinator.task_graph import NodeStatus

    if node.retries < node.max_retries:
        node.retries += 1
        logger.info("[COORDINATOR_RECOVERY] retry node=%s attempt=%d reason=world_state_condition",
                    node.node_id, node.retries)
        graph.nodes[node.node_id].status = NodeStatus.PENDING
        return {}

    graph.mark_failed(node.node_id, f"success_condition not met: {reason}")

    if node.rollback_goal:
        try:
            from api.agents.agent_types import AgentType
            logger.info("[COORDINATOR_ROLLBACK] node=%s goal=%r", node.node_id, node.rollback_goal[:80])
            asyncio.create_task(agent_runtime.launch(
                goal=node.rollback_goal,
                agent_type=AgentType.GENERIC,
                ws_send_fn=coord_task.ws_send_fn,
                context={"rollback_for_node": node.node_id, "coordinator_task_id": coord_task.task_id},
            ))
        except Exception:
            logger.debug("[COORDINATOR_ROLLBACK] dispatch failed", exc_info=True)

    return {}


async def _run_verifier_node(node: Any, graph: Any, context: dict) -> dict:
    """Inline verification — no agent launch needed."""
    from api.agents.coordinator.coordinator_verifier import coordinator_verifier
    try:
        ok = await coordinator_verifier.verify_node(node, context)
        if ok:
            graph.mark_done(node.node_id, {"verified": True})
            logger.info("[COORDINATOR_VERIFY] node=%s OK", node.node_id)
            return {"verified": True}
        else:
            graph.mark_failed(node.node_id, "Verification failed")
            logger.info("[COORDINATOR_VERIFY] node=%s FAIL", node.node_id)
            return {"verified": False}
    except Exception as e:
        graph.mark_failed(node.node_id, str(e))
        return {"verified": False}


async def _run_personality_node(node: Any, graph: Any, context: dict) -> dict:
    """Apply personality polish to accumulated output."""
    try:
        from api.agents.personality.personality_engine import personality_engine
        raw = context.get("result_summary", "") or node.goal
        polished = personality_engine.polish_response(raw)
        graph.mark_done(node.node_id, {"final_response": polished})
        return {"final_response": polished}
    except Exception as e:
        logger.debug("[COORDINATOR] personality node error: %s", e)
        fallback = context.get("result_summary", "Task completed.")
        graph.mark_done(node.node_id, {"final_response": fallback})
        return {"final_response": fallback}


def _build_raw_summary(graph: Any, output: dict, duration_s: float, verified: bool) -> str:
    """Build raw summary before personality polish."""
    from api.agents.coordinator.task_graph import NodeStatus
    done = [n for n in graph.nodes.values() if n.status == NodeStatus.DONE]

    if output.get("final_response"):
        return output["final_response"]
    if output.get("project_path"):
        path  = output["project_path"]
        url   = output.get("preview_url", "localhost:5173")
        return f"Done. I built the project at {path} and opened the preview at {url}."
    if output.get("research_summary"):
        summary = output["research_summary"]
        return f"Done. Here's what I found: {summary[:200]}"
    if output.get("result_summary"):
        return output["result_summary"][:200]
    if graph.is_failed():
        failed_count = sum(1 for n in graph.nodes.values() if n.status == NodeStatus.FAILED)
        return (f"I ran into some issues. {len(done)} of {len(graph.nodes)} steps completed, "
                f"{failed_count} failed.")
    return f"Done. Completed {len(done)} steps in {duration_s:.0f}s."
