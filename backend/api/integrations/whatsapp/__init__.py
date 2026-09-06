from .models import WAAction, WAErrorCode, WhatsAppRequest, WhatsAppResult
from .transport import WhatsAppTransport
from .openwa_transport import OpenWATransport
from .baileys_transport import BaileysTransport
from .contact_resolver import ContactResolver, ContactResolution
from .send_security import SendPathVerdict, validate_sendable_path, detect_media_kind
from .send_idempotency import (
    PersistentSendStore,
    ClaimResult,
    payload_hash,
    get_default_send_store,
)
from .file_send import FileSendPlanner, FileSendPlan, FileCandidate
from .wa_context import (
    WhatsAppContext,
    WAInteraction,
    ContactReference,
    is_contextual_contact_reference,
    WAArtifact,
    ArtifactReference,
    is_contextual_artifact_reference,
    artifact_reference_kind,
    get_default_context,
)
from .screenshot_resolver import (
    ScreenshotResolver,
    ScreenshotCandidate,
    ScreenshotResolution,
)
from .wa_ui_adapter import (
    WhatsAppUIAdapter,
    WhatsAppUITarget,
    UIActionReport,
    get_default_ui_adapter,
)
from .wa_identity import (
    WhatsAppContactIdentity,
    WhatsAppIdentityStore,
    get_default_identity_store,
)
from .wa_intent import WAIntent, parse_wa_intent
from .wa_command_handler import (
    WACommandHandler,
    WAOutcome,
    ResolvedContact,
    LatencyTimer,
    get_default_command_handler,
)

__all__ = [
    "WAAction",
    "WAErrorCode",
    "WhatsAppRequest",
    "WhatsAppResult",
    "WhatsAppTransport",
    "OpenWATransport",
    "BaileysTransport",
    "ContactResolver",
    "ContactResolution",
    "SendPathVerdict",
    "validate_sendable_path",
    "detect_media_kind",
    "PersistentSendStore",
    "ClaimResult",
    "payload_hash",
    "get_default_send_store",
    "FileSendPlanner",
    "FileSendPlan",
    "FileCandidate",
    # Phase 3 — conversational context + screenshot resolution
    "WhatsAppContext",
    "WAInteraction",
    "ContactReference",
    "is_contextual_contact_reference",
    "get_default_context",
    # Phase 3 — artifact/file context + contact carryover
    "WAArtifact",
    "ArtifactReference",
    "is_contextual_artifact_reference",
    "artifact_reference_kind",
    "ScreenshotResolver",
    "ScreenshotCandidate",
    "ScreenshotResolution",
    # Phase 3 — visual surface (open/show conversations; never sends)
    "WhatsAppUIAdapter",
    "WhatsAppUITarget",
    "UIActionReport",
    "get_default_ui_adapter",
    # Phase 4 — canonical identity cache, deterministic intent parser,
    # command orchestration
    "WhatsAppContactIdentity",
    "WhatsAppIdentityStore",
    "get_default_identity_store",
    "WAIntent",
    "parse_wa_intent",
    "WACommandHandler",
    "WAOutcome",
    "ResolvedContact",
    "LatencyTimer",
    "get_default_command_handler",
]
