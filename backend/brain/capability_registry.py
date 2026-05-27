"""
Capability Registry — what Xyron can do, described at three fidelity levels.

Each capability carries public, technical, and debug descriptions, status,
owning agents, and a demo_value score (0.0–1.0) for prioritising demos.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Status = Literal["active", "partial", "planned"]


@dataclass
class Capability:
    id:                   str
    name:                 str
    public_description:   str
    technical_description: str
    status:               Status
    agents:               list[str]  = field(default_factory=list)
    demo_value:           float      = 0.5

    def to_dict(self, mode: str = "PUBLIC") -> dict:
        base = {
            "id":       self.id,
            "name":     self.name,
            "status":   self.status,
            "agents":   self.agents,
            "demo_value": self.demo_value,
        }
        if mode == "PUBLIC":
            base["description"] = self.public_description
        elif mode == "TECHNICAL":
            base["description"] = self.technical_description
        else:
            base["description"] = self.technical_description
        return base


_CAPABILITIES: list[Capability] = [
    Capability(
        id="voice_system",
        name="Voice Interaction",
        public_description=(
            "Understands your voice in real time, speaks back with emotional inflection, "
            "and adapts tone to the situation."
        ),
        technical_description=(
            "Wake word detection (OWW + tiny Whisper). STT: faster-whisper (local CUDA). "
            "TTS: Kokoro-ONNX tier-1, edge-tts tier-2, pyttsx3 fallback. "
            "10-state mood machine drives prosody via prosody_planner."
        ),
        status="active",
        agents=["voice_agent"],
        demo_value=1.0,
    ),
    Capability(
        id="system_control",
        name="System Control",
        public_description=(
            "Controls applications, files, windows, volume, and system state "
            "through natural language — no keyboard required."
        ),
        technical_description=(
            "~300 registered tool functions via PowerShell WSL2 bridge. "
            "Covers app launch, file ops, system info, screenshots, window management."
        ),
        status="active",
        agents=["system_agent"],
        demo_value=0.95,
    ),
    Capability(
        id="app_launching",
        name="App Launching",
        public_description="Opens any application on this machine on command.",
        technical_description="system_tools.open_application() via PowerShell Start-Process bridge.",
        status="active",
        agents=["system_agent"],
        demo_value=0.8,
    ),
    Capability(
        id="file_management",
        name="File Management",
        public_description="Creates, moves, renames, and organises files and folders intelligently.",
        technical_description="File ops via PowerShell bridge; context memory tracks recent paths for pronoun resolution.",
        status="active",
        agents=["system_agent"],
        demo_value=0.75,
    ),
    Capability(
        id="coding_assistance",
        name="Coding Assistance",
        public_description=(
            "Helps with code — explains, writes, debugs, and runs tests — "
            "while being aware of what's on screen."
        ),
        technical_description=(
            "DevAgent: VS Code awareness, terminal context, project memory, "
            "dev_observer background monitor, takeover mode."
        ),
        status="active",
        agents=["dev_agent"],
        demo_value=0.9,
    ),
    Capability(
        id="automation",
        name="Automation",
        public_description="Executes multi-step workflows — work mode, chill mode, home mode, custom routines.",
        technical_description=(
            "AutomationAgent: work_mode, chill_mode, home_mode, takeover_mode triggers. "
            "Plans via planner.py. HITL approval gate for risky operations."
        ),
        status="active",
        agents=["automation_agent"],
        demo_value=0.85,
    ),
    Capability(
        id="memory_context",
        name="Memory & Context",
        public_description=(
            "Remembers preferences, past conversations, and important events "
            "so every session builds on the last."
        ),
        technical_description=(
            "Short-term: session deque. Long-term: ~/.ai-operator/memory.json + ChromaDB semantic store. "
            "Episodic: SQLite per-turn log with tool patterns."
        ),
        status="active",
        agents=["memory_agent"],
        demo_value=0.85,
    ),
    Capability(
        id="emotional_cognition",
        name="Emotional Cognition",
        public_description=(
            "Reads the emotional tone of conversations and responds with natural variation "
            "in voice, pacing, and personality."
        ),
        technical_description=(
            "EmotionAgent: 10-state mood machine, emotion_tts_mapper, prosody curves, "
            "expression_engine, self_upgrade detection, emotional intent guard."
        ),
        status="active",
        agents=["emotion_agent"],
        demo_value=0.95,
    ),
    Capability(
        id="command_center",
        name="Command Center UI",
        public_description=(
            "A live dashboard showing system state, conversation history, approvals, "
            "and direct control over all systems."
        ),
        technical_description=(
            "Next.js 15 App Router, React 19, Tailwind, Framer Motion. "
            "Polls /api/v1/* endpoints. SSE event stream for live updates."
        ),
        status="active",
        agents=[],
        demo_value=0.9,
    ),
    Capability(
        id="takeover_mode",
        name="Takeover Mode",
        public_description=(
            "Drives VS Code directly — reads errors, executes code, "
            "and works through problems hands-on."
        ),
        technical_description="screen_tools + dev_observer; writes to active terminal via PowerShell bridge.",
        status="active",
        agents=["dev_agent", "screen_agent"],
        demo_value=0.85,
    ),
    Capability(
        id="audience_mode",
        name="Audience Mode",
        public_description=(
            "Switches into a polished presentation style — ideal for demos, "
            "videos, and live recordings."
        ),
        technical_description="self_intro_engine.py AUDIENCE style; identity_policy.PUBLIC mode enforced.",
        status="active",
        agents=["emotion_agent"],
        demo_value=0.8,
    ),
    Capability(
        id="proactive_intelligence",
        name="Proactive Intelligence",
        public_description="Notices patterns and makes suggestions before being asked.",
        technical_description="proactive_service.py: time-based triggers + episodic pattern analysis.",
        status="partial",
        agents=["automation_agent"],
        demo_value=0.7,
    ),
    Capability(
        id="screen_awareness",
        name="Screen Awareness",
        public_description="Sees what's on screen and uses that context to give smarter responses.",
        technical_description="screen_context_service.py: periodic screenshot analysis via vision model.",
        status="partial",
        agents=["screen_agent"],
        demo_value=0.75,
    ),
    Capability(
        id="browser_automation",
        name="Browser Automation",
        public_description="Controls web browsers to fill forms, scrape data, and automate web tasks.",
        technical_description="browser_tools.py + Playwright MCP server (WhatsApp MCP).",
        status="partial",
        agents=["screen_agent", "automation_agent"],
        demo_value=0.7,
    ),
    Capability(
        id="local_ai_stack",
        name="Local-First AI Stack",
        public_description=(
            "All intelligence runs entirely on this machine. "
            "No data leaves the device. No cloud dependency."
        ),
        technical_description=(
            "Ollama (llama3.2:3b, mistral:7b, nomic-embed-text), "
            "Kokoro-ONNX, faster-whisper, ChromaDB — all local."
        ),
        status="active",
        agents=[],
        demo_value=1.0,
    ),
    Capability(
        id="channel_agents",
        name="Channel Agents",
        public_description=(
            "Future capability: agents that manage WhatsApp, Gmail, LinkedIn, "
            "Instagram, and GitHub on your behalf."
        ),
        technical_description="ChannelAgent placeholder; architecture designed; integrations disabled pending Phase 2.",
        status="planned",
        agents=["channel_agent"],
        demo_value=0.6,
    ),
    Capability(
        id="semantic_understanding",
        name="Semantic Understanding",
        public_description=(
            "Understands meaning, not just keywords — "
            "broken English, Urdu-English, and indirect requests all work."
        ),
        technical_description=(
            "3-tier routing: fast rules → nomic-embed-text embeddings → llama3.2:3b JSON judge. "
            "ChromaDB intent store with 18 intent categories."
        ),
        status="active",
        agents=[],
        demo_value=0.9,
    ),
    Capability(
        id="autonomous_planning",
        name="Autonomous Planning",
        public_description=(
            "Plans and executes multi-step tasks autonomously — "
            "with confirmation for anything risky."
        ),
        technical_description=(
            "planner.py: goal → steps → risk assessment → autonomy gate. "
            "safety_gate.py: confirmation required for delete/send/post/shell."
        ),
        status="partial",
        agents=["automation_agent"],
        demo_value=0.8,
    ),
]


class CapabilityRegistry:
    def __init__(self) -> None:
        self._caps: dict[str, Capability] = {c.id: c for c in _CAPABILITIES}

    def all(self, mode: str = "PUBLIC") -> list[dict]:
        return [c.to_dict(mode) for c in self._caps.values()]

    def active(self, mode: str = "PUBLIC") -> list[dict]:
        return [c.to_dict(mode) for c in self._caps.values() if c.status == "active"]

    def by_agent(self, agent_id: str, mode: str = "PUBLIC") -> list[dict]:
        return [c.to_dict(mode) for c in self._caps.values() if agent_id in c.agents]

    def get(self, cap_id: str) -> Capability | None:
        return self._caps.get(cap_id)

    def summary(self) -> str:
        active = sum(1 for c in self._caps.values() if c.status == "active")
        partial = sum(1 for c in self._caps.values() if c.status == "partial")
        planned = sum(1 for c in self._caps.values() if c.status == "planned")
        return f"{active} active, {partial} partial, {planned} planned capabilities"


capability_registry = CapabilityRegistry()
