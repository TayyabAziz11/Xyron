from enum import Enum
from dataclasses import dataclass, field
from typing import Dict


class Tier(str, Enum):
    FREE       = "free"
    PRO        = "pro"
    ENTERPRISE = "enterprise"


class Feature(str, Enum):
    # Free
    VOICE_ASSISTANT    = "voiceAssistant"
    WAKE_WORD          = "wakeWord"
    SYSTEM_TOOLS       = "systemTools"
    OPEN_APPS          = "openApps"
    FILE_BROWSER       = "fileBrowser"
    SETTINGS_CONTROL   = "settingsControl"
    VOLUME_CONTROL     = "volumeControl"
    WORKFLOWS          = "workflows"
    OFFLINE_AI         = "offlineAI"
    BASIC_MEMORY       = "basicMemory"
    WORK_MODE          = "workMode"
    # Pro
    ADVANCED_AI        = "advancedAI"
    PERSISTENT_MEMORY  = "persistentMemory"
    AUTONOMOUS_AGENTS  = "autonomousAgents"
    BROWSER_CONTROL    = "browserControl"
    EMAIL_INTEGRATION  = "emailIntegration"
    CALENDAR_INTEGRATION = "calendarIntegration"
    SMART_WORKFLOWS    = "smartWorkflows"
    CLOUD_SYNC         = "cloudSync"
    PREMIUM_VOICE      = "premiumVoice"
    DESKTOP_AUTOMATION = "desktopAutomation"
    PRIORITY_MODELS    = "priorityModels"
    # Enterprise
    TEAM_AGENTS        = "teamAgents"
    SHARED_MEMORY      = "sharedMemory"
    ORG_DASHBOARD      = "orgDashboard"
    ADMIN_CONTROLS     = "adminControls"
    API_ACCESS         = "apiAccess"
    SELF_HOSTED        = "selfHosted"
    WORKFLOW_ORCHESTRATION = "workflowOrchestration"
    COMPANY_COPILOTS   = "companyCopilots"


_FREE = {
    Feature.VOICE_ASSISTANT, Feature.WAKE_WORD, Feature.SYSTEM_TOOLS,
    Feature.OPEN_APPS, Feature.FILE_BROWSER, Feature.SETTINGS_CONTROL,
    Feature.VOLUME_CONTROL, Feature.WORKFLOWS, Feature.OFFLINE_AI,
    Feature.BASIC_MEMORY, Feature.WORK_MODE,
}
_PRO = _FREE | {
    Feature.ADVANCED_AI, Feature.PERSISTENT_MEMORY, Feature.AUTONOMOUS_AGENTS,
    Feature.BROWSER_CONTROL, Feature.EMAIL_INTEGRATION, Feature.CALENDAR_INTEGRATION,
    Feature.SMART_WORKFLOWS, Feature.CLOUD_SYNC, Feature.PREMIUM_VOICE,
    Feature.DESKTOP_AUTOMATION, Feature.PRIORITY_MODELS,
}
_ENTERPRISE = _PRO | {
    Feature.TEAM_AGENTS, Feature.SHARED_MEMORY, Feature.ORG_DASHBOARD,
    Feature.ADMIN_CONTROLS, Feature.API_ACCESS, Feature.SELF_HOSTED,
    Feature.WORKFLOW_ORCHESTRATION, Feature.COMPANY_COPILOTS,
}

TIER_FEATURES: Dict[Tier, set] = {
    Tier.FREE:       _FREE,
    Tier.PRO:        _PRO,
    Tier.ENTERPRISE: _ENTERPRISE,
}


def can_access(tier: Tier, feature: Feature) -> bool:
    return feature in TIER_FEATURES.get(tier, set())
