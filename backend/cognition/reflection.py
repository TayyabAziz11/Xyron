"""
Self-reflection engine — analyzes recent interactions and refines personality.

Runs every 30 minutes in the background. One GPT call per cycle.
Insights stored to ChromaDB with metadata type=reflection.
If the same suggestion appears 3+ cycles, personality adapts automatically.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_REFLECTION_SCHEMA = """{
  "summary": "<one sentence describing this session>",
  "focus_quality": "<high|medium|low>",
  "interruptions": <integer count of context switches>,
  "goal_progress": "<description of progress toward active goals>",
  "observation": "<one key behavioral pattern noticed>",
  "suggestion": "<one concrete improvement for Xyron's behavior>",
  "patterns": ["<pattern1>", "<pattern2>"]
}"""


class ReflectionEngine:

    async def run_reflection_cycle(self) -> dict:
        """
        One reflection cycle:
          1. Reads last 50 episodic turns
          2. Reads active goals
          3. One GPT call → JSON insight
          4. Stores insight to ChromaDB
          5. Checks for repeated suggestions → adapts personality
        """
        logger.info("[Reflection] starting cycle")
        start = time.time()

        turns = await self._load_turns(n=50)
        goals = self._load_goals()

        if not turns and not goals:
            logger.debug("[Reflection] no data — skipping cycle")
            return {"skipped": True, "reason": "no_data"}

        insight = await self._call_llm(turns, goals)
        if not insight:
            logger.debug("[Reflection] LLM unavailable — skipping storage")
            return {"skipped": True, "reason": "llm_unavailable"}

        self._store_insight(insight)
        self._maybe_adapt_personality(insight)

        elapsed = round(time.time() - start, 2)
        insight["elapsed_s"] = elapsed
        logger.info("[Reflection] cycle done in %.2fs  focus=%s suggestion=%r",
                    elapsed, insight.get("focus_quality"), insight.get("suggestion", "")[:60])
        return insight

    async def start_loop(self, interval_minutes: int = 30) -> None:
        """Background loop — sleeps between cycles. Call via asyncio.create_task()."""
        logger.info("[Reflection] loop started — interval=%dmin", interval_minutes)
        while True:
            await asyncio.sleep(interval_minutes * 60)
            try:
                await self.run_reflection_cycle()
            except Exception as exc:
                logger.warning("[Reflection] cycle error: %s", exc)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    async def _load_turns(n: int = 50) -> list[dict]:
        try:
            from api.services.episodic_memory import episodic_memory
            raw = await asyncio.to_thread(episodic_memory.recent, n)
            result = []
            for t in (raw or []):
                result.append({
                    "role": getattr(t, "role", "") or t.get("role", ""),
                    "text": getattr(t, "text", "") or t.get("text", ""),
                })
            return result
        except Exception as exc:
            logger.debug("[Reflection] load_turns error: %s", exc)
            return []

    @staticmethod
    def _load_goals() -> list[dict]:
        try:
            from cognition.goals import goal_tracker
            return [
                {"description": g.description, "priority": g.priority}
                for g in goal_tracker.get_active_goals()
            ]
        except Exception as exc:
            logger.debug("[Reflection] load_goals error: %s", exc)
            return []

    @staticmethod
    async def _call_llm(turns: list[dict], goals: list[dict]) -> dict | None:
        try:
            from api.services.openai_client import openai_client
            if not openai_client.available:
                return None

            turns_text = "\n".join(
                f"{t['role'].upper()}: {t['text'][:150]}"
                for t in turns[-30:]
                if t.get("text")
            )
            goals_text = "\n".join(
                f"- {g['description']} (priority {g['priority']})"
                for g in goals
            ) or "None"

            user_content = (
                f"RECENT INTERACTIONS ({len(turns)} turns):\n{turns_text}"
                f"\n\nACTIVE GOALS:\n{goals_text}"
            )

            messages = [
                {"role": "system", "content": (
                    "You are Xyron's self-reflection engine. "
                    "Analyze these interactions and return JSON only — no markdown, no explanation. "
                    f"Schema:\n{_REFLECTION_SCHEMA}"
                )},
                {"role": "user", "content": user_content},
            ]

            raw = await asyncio.to_thread(
                openai_client.generate,
                messages,
                "gpt-4o-mini",
                400,
                0.3,
            )
            if not raw:
                return None

            # Strip markdown fences if present
            cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(cleaned)

        except json.JSONDecodeError as exc:
            logger.warning("[Reflection] JSON parse error: %s", exc)
            return None
        except Exception as exc:
            logger.warning("[Reflection] LLM call error: %s", exc)
            return None

    @staticmethod
    def _store_insight(insight: dict) -> None:
        try:
            from cognition.memory.semantic_store import semantic_store
            summary = insight.get("summary", "reflection cycle")
            text = (
                f"Reflection: {summary}. "
                f"Observation: {insight.get('observation', '')}. "
                f"Suggestion: {insight.get('suggestion', '')}."
            )
            semantic_store.remember(text, metadata={
                "type":          "reflection",
                "focus_quality": str(insight.get("focus_quality", "")),
                "suggestion":    str(insight.get("suggestion", ""))[:200],
                "timestamp":     str(time.time()),
            })
        except Exception as exc:
            logger.debug("[Reflection] store_insight error: %s", exc)

    @staticmethod
    def _maybe_adapt_personality(insight: dict) -> None:
        """If the same suggestion keyword appears 3+ times in recent reflections, adapt."""
        suggestion = insight.get("suggestion", "").lower()
        if not suggestion:
            return
        try:
            from cognition.memory.semantic_store import semantic_store
            recent = semantic_store.recall("reflection suggestion", n=10)
            reflection_suggestions = [
                r.get("metadata", {}).get("suggestion", "").lower()
                for r in recent
                if r.get("metadata", {}).get("type") == "reflection"
            ]

            # Map keyword → (attribute, value) adjustments
            rules = [
                (("brief", "shorter", "too long", "concise"), "verbosity", "brief"),
                (("verbose", "more detail", "expand"),        "verbosity", "verbose"),
                (("formal", "professional"),                  "tone",      "formal"),
                (("casual", "friendly", "warmer"),            "tone",      "casual"),
                (("direct", "blunt"),                         "tone",      "direct"),
            ]
            for keywords, attr, val in rules:
                hits = sum(
                    1 for s in reflection_suggestions
                    if any(kw in s for kw in keywords)
                )
                if hits >= 3:
                    from cognition.personality import personality
                    setattr(personality, attr, val)
                    logger.info(
                        "[Reflection] personality adapted: %s=%r after %d matching suggestions",
                        attr, val, hits,
                    )
                    break
        except Exception as exc:
            logger.debug("[Reflection] adapt_personality error: %s", exc)


reflection_engine = ReflectionEngine()
