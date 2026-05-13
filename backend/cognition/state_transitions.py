"""
State transition helpers — the only sanctioned way to advance AttentionLevel.

Call these at the correct points in the voice pipeline and command service:
  - User starts speaking      → transition_to_listening()
  - Transcript received       → transition_to_processing()
  - Response starts streaming → transition_to_speaking()
  - Response ends / idle      → transition_to_idle()
"""
from __future__ import annotations

import logging

from .cognitive_state import AttentionLevel, cognitive_state

logger = logging.getLogger(__name__)


def transition_to_listening() -> None:
    prev = cognitive_state.attention
    cognitive_state.update(attention=AttentionLevel.LISTENING)
    logger.debug("[STATE] %s → LISTENING", prev)


def transition_to_processing() -> None:
    prev = cognitive_state.attention
    cognitive_state.update(
        attention=AttentionLevel.PROCESSING,
        turn_count=cognitive_state.turn_count + 1,
    )
    logger.debug("[STATE] %s → PROCESSING  turn=%d", prev, cognitive_state.turn_count)


def transition_to_speaking() -> None:
    prev = cognitive_state.attention
    cognitive_state.update(attention=AttentionLevel.SPEAKING)
    logger.debug("[STATE] %s → SPEAKING", prev)


def transition_to_idle() -> None:
    prev = cognitive_state.attention
    cognitive_state.update(attention=AttentionLevel.IDLE)
    logger.debug("[STATE] %s → IDLE", prev)
