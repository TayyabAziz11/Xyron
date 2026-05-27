"""
Intent Example Store — training examples for the semantic understanding layer.

18 intent categories with diverse natural-language examples including
broken English, Roman Urdu, misheard variants, and indirect phrasing.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IntentExample:
    intent:     str   # e.g. "self_upgrade"
    route:      str   # "tool|agent|conversation|emotional|intro|clarify"
    target:     str   # e.g. "emotion_agent"
    utterance:  str
    emotion_hint: str = "neutral"


INTENT_EXAMPLES: list[IntentExample] = [

    # ── self_upgrade ──────────────────────────────────────────────────────────
    *[IntentExample("self_upgrade", "emotional", "emotion_agent", u, "warm_surprise") for u in [
        "make your brain better",
        "upgrade your memory",
        "give yourself better memory",
        "improve your intelligence",
        "make yourself smarter",
        "update your knowledge",
        "I'm thinking of upgrading your memory",
        "enhance your capabilities",
        "boost your processing",
        "let's improve your brain",
        "aapki memory upgrade karo",
        "zyada smart bano",
        "make yourself more capable",
        "upgrade karo apne aap ko",
        "I want to upgrade you",
        "let me make you better",
        "I'm upgrading your systems",
        "add more intelligence to yourself",
    ]],

    # ── frustration ───────────────────────────────────────────────────────────
    *[IntentExample("frustration", "emotional", "emotion_agent", u, "empathy") for u in [
        "this bug is killing me",
        "I'm so frustrated right now",
        "nothing is working",
        "this is so annoying",
        "ugh this code is broken",
        "I can't figure this out",
        "I've been stuck on this for hours",
        "yaar kuch kaam nahi kar raha",
        "everything is broken",
        "I'm losing my mind",
        "this is so frustrating",
        "I hate this bug",
        "nothing makes sense",
        "ughhh",
    ]],

    # ── intro_audience ────────────────────────────────────────────────────────
    *[IntentExample("intro_audience", "intro", "self_intro", u, "confident") for u in [
        "introduce yourself to my audience",
        "explain yourself to my viewers",
        "say hello to everyone",
        "tell them who you are",
        "introduce yourself for the video",
        "introduce yourself for the stream",
        "audience ko bata apne baare mein",
        "explain yourself to my followers",
        "present yourself",
        "do your intro for the presentation",
        "who are you for the audience",
        "tell everyone what you are",
    ]],

    # ── intro_technical ───────────────────────────────────────────────────────
    *[IntentExample("intro_technical", "intro", "self_intro", u, "neutral") for u in [
        "explain how you work technically",
        "what's your architecture",
        "tell me your tech stack",
        "how are you built",
        "what are you made of",
        "explain yourself as a developer",
        "technical introduction",
        "apni architecture batao",
        "under the hood kya hai",
        "what models do you use",
        "describe your system",
        "how does your voice work",
    ]],

    # ── intro_short ───────────────────────────────────────────────────────────
    *[IntentExample("intro_short", "intro", "self_intro", u, "neutral") for u in [
        "introduce yourself",
        "who are you",
        "what are you",
        "tell me about yourself",
        "your name",
        "aap kaun ho",
        "tum kya ho",
        "xyron kaun hai",
        "zairon explain yourself",
        "xyron batao",
        "what is xyron",
    ]],

    # ── open_app ──────────────────────────────────────────────────────────────
    *[IntentExample("open_app", "tool", "system_agent", u, "neutral") for u in [
        "open chrome",
        "open google chrome",
        "launch vs code",
        "open visual studio code",
        "start spotify",
        "chrome kholo",
        "vs code kholo",
        "open notepad",
        "launch discord",
        "open firefox",
        "start the browser",
        "open my editor",
        "open task manager",
        "launch file explorer",
    ]],

    # ── file_action ───────────────────────────────────────────────────────────
    *[IntentExample("file_action", "tool", "system_agent", u, "neutral") for u in [
        "delete that folder",
        "move this file to downloads",
        "create a new folder",
        "rename this file",
        "copy everything to desktop",
        "open the downloads folder",
        "folder delete karo",
        "naya folder banao",
        "move the project files",
        "delete temp files",
        "clean up the desktop",
        "show me what's in documents",
    ]],

    # ── system_status ─────────────────────────────────────────────────────────
    *[IntentExample("system_status", "tool", "system_agent", u, "neutral") for u in [
        "what's my battery level",
        "check the system health",
        "how much RAM am I using",
        "what processes are running",
        "show CPU usage",
        "system status check karo",
        "is the system running well",
        "what time is it",
        "battery kitni hai",
        "check disk space",
        "how is the machine doing",
    ]],

    # ── dev_help ──────────────────────────────────────────────────────────────
    *[IntentExample("dev_help", "agent", "dev_agent", u, "focused") for u in [
        "help me debug this",
        "explain this error",
        "write a function for",
        "what's wrong with my code",
        "refactor this",
        "help me write tests",
        "explain this function",
        "fix this bug",
        "code mein error hai",
        "test likhne mein madad karo",
        "review my code",
        "optimize this function",
        "write a python script to",
    ]],

    # ── work_mode ─────────────────────────────────────────────────────────────
    *[IntentExample("work_mode", "agent", "automation_agent", u, "focused") for u in [
        "prepare my work setup",
        "set up work mode",
        "start work mode",
        "get me ready to work",
        "open my work apps",
        "kaam ka mode on karo",
        "focus mode start karo",
        "set up for coding",
        "prepare my dev environment",
        "start my day",
        "work mode activate",
    ]],

    # ── chill_mode ────────────────────────────────────────────────────────────
    *[IntentExample("chill_mode", "agent", "automation_agent", u, "relaxed") for u in [
        "switch to chill mode",
        "relax mode",
        "I'm done working",
        "play some music",
        "chill karna chahta hoon",
        "entertainment mode",
        "free time mode",
        "set up for relaxing",
        "casual mode",
        "lofi mode",
    ]],

    # ── takeover_mode ─────────────────────────────────────────────────────────
    *[IntentExample("takeover_mode", "agent", "automation_agent", u, "focused") for u in [
        "take over my screen",
        "takeover mode",
        "drive VS Code",
        "take control",
        "start coding for me",
        "work autonomously",
        "you take the wheel",
        "autonomous mode",
        "VS Code pe kaam karo",
        "handle it yourself",
    ]],

    # ── home_mode ─────────────────────────────────────────────────────────────
    *[IntentExample("home_mode", "agent", "automation_agent", u, "relaxed") for u in [
        "I'm home",
        "home mode",
        "I just got home",
        "set up home environment",
        "ghar aa gaya",
        "home setup",
        "personal mode",
        "evening mode",
    ]],

    # ── explain_capability ────────────────────────────────────────────────────
    *[IntentExample("explain_capability", "conversation", "brain", u, "neutral") for u in [
        "what can you do",
        "show me your capabilities",
        "list your features",
        "what are you capable of",
        "what's possible with you",
        "tum kya kar sakte ho",
        "apni capabilities batao",
        "what do you support",
        "what skills do you have",
        "show me everything you can do",
    ]],

    # ── ask_future_desire ─────────────────────────────────────────────────────
    *[IntentExample("ask_future_desire", "emotional", "emotion_agent", u, "ambitious") for u in [
        "what do you want next",
        "what's your next upgrade",
        "what are you planning to become",
        "what do you dream about",
        "what features do you want",
        "aage kya chahiye tumhe",
        "what are you working towards",
        "what's your future goal",
        "where are you headed",
        "what would make you better",
    ]],

    # ── automation_request ────────────────────────────────────────────────────
    *[IntentExample("automation_request", "agent", "automation_agent", u, "neutral") for u in [
        "automate this task",
        "set up a workflow",
        "run this every day",
        "schedule this",
        "create a routine",
        "automate my morning",
        "har roz ye karo",
        "workflow banao",
        "daily task set karo",
        "run this automatically",
    ]],

    # ── memory_query ──────────────────────────────────────────────────────────
    *[IntentExample("memory_query", "agent", "memory_agent", u, "neutral") for u in [
        "what do you remember about me",
        "what did we talk about last time",
        "do you remember when",
        "what's my preference for",
        "recall the last project",
        "mujhe kya bataya tha",
        "pichli baat yaad hai",
        "what's in your memory",
        "search your memory for",
        "do you know my name",
    ]],

    # ── screen_help ───────────────────────────────────────────────────────────
    *[IntentExample("screen_help", "agent", "screen_agent", u, "neutral") for u in [
        "what's on my screen",
        "read the error on screen",
        "what does this say",
        "take a screenshot",
        "analyze my screen",
        "screen pe kya hai",
        "error read karo",
        "what's open right now",
        "describe what you see",
        "check the current window",
        "what app is active",
    ]],

    # ── open_settings ─────────────────────────────────────────────────────────
    # Covers Windows Settings deep-links (display, sound, bluetooth, etc.)
    # Includes broken English, Roman Urdu, and natural spoken phrasing.
    *[IntentExample("open_settings", "tool", "open_system_settings", u, "neutral") for u in [
        # display / screen / monitor
        "open display settings",
        "open windows display settings",
        "display settings",
        "screen settings",
        "monitor settings",
        "show me display settings",
        "change display settings",
        "can you open display settings for me",
        "display settings kholo",
        "display wala setting kholo",
        "screen setting open karo",
        "monitor resolution open",
        "open display setting please",
        "screen resolution settings",
        "change my screen resolution",
        # sound
        "open sound settings",
        "sound settings",
        "audio settings",
        "sound wali settings kholo",
        # bluetooth
        "open bluetooth settings",
        "bluetooth settings",
        "bluetooth on karo",
        # wifi / network
        "open wifi settings",
        "open network settings",
        "network settings kholo",
        # privacy
        "open privacy settings",
        "privacy settings",
        # update
        "open windows update",
        "check for updates",
        "update settings",
        # power
        "open power settings",
        "power and sleep settings",
        # apps
        "open apps settings",
        "apps and features",
        # general
        "open system settings",
        "take me to settings",
        "windows settings open karo",
    ]],
]


def examples_for_intent(intent: str) -> list[IntentExample]:
    return [e for e in INTENT_EXAMPLES if e.intent == intent]


def all_utterances() -> list[tuple[str, str, str]]:
    """Returns [(utterance, intent, route), ...]"""
    return [(e.utterance, e.intent, e.route) for e in INTENT_EXAMPLES]


INTENT_CATEGORIES = sorted({e.intent for e in INTENT_EXAMPLES})
