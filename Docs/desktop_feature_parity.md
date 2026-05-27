# Desktop Feature Parity — Tauri Target
_Generated: 2026-05-18_

This document defines the exact feature set the Tauri desktop must have at ship time — organized as a verification checklist.

---

## Routes / Pages

| # | Route | Web Source | Desktop Status | Notes |
|---|---|---|:---:|---|
| 1 | `/` → Dashboard | `dashboard/page.tsx` | ❌ | Primary landing page |
| 2 | `/commands` | `command-center/page.tsx` | ⚠️ | Partial — needs all 20+ sub-components |
| 3 | `/activity` | `activity/page.tsx` | ⚠️ | Partial — needs ActivityItem |
| 4 | `/approvals` | `approvals/page.tsx` | ❌ | Full HITL approval flow |
| 5 | `/history` | `history/page.tsx` | ❌ | |
| 6 | `/integrations` | `integrations/page.tsx` | ❌ | |
| 7 | `/settings` | `settings/page.tsx` | ⚠️ | Needs ProfileSwitcher |
| 8 | `/stats` | `stats/page.tsx` | ❌ | |
| 9 | `/workflows` | `workflows/page.tsx` | ❌ | |

---

## Components — Web Parity

### Layout
| Component | Web File | Desktop | Priority |
|---|---|:---:|---|
| AppShell | `layout/AppShell.tsx` | ❌ | P0 |
| Header | `layout/Header.tsx` | ❌ | P0 |
| Sidebar (full) | `layout/Sidebar.tsx` | ⚠️ | P0 |
| PageTransition | `layout/PageTransition.tsx` | ⚠️ | P1 |

### Dashboard
| Component | Web File | Desktop | Priority |
|---|---|:---:|---|
| BrainPanel | `dashboard/BrainPanel.tsx` | ❌ | P0 |
| BrainCanvas | `dashboard/BrainCanvas.tsx` | ❌ | P0 |
| ActivityFeed | `dashboard/ActivityFeed.tsx` | ❌ | P0 |
| ParticleCanvas | `dashboard/ParticleCanvas.tsx` | ❌ | P1 |
| QuickCommand | `dashboard/QuickCommand.tsx` | ❌ | P0 |
| StatCard | `dashboard/StatCard.tsx` | ❌ | P1 |
| IntegrationGrid | `dashboard/IntegrationGrid.tsx` | ❌ | P1 |

### Voice
| Component | Web File | Desktop | Priority |
|---|---|:---:|---|
| ConversationThread | `voice/ConversationThread.tsx` | ❌ | P0 |
| VoiceSessionPanel | `voice/VoiceSessionPanel.tsx` | ❌ | P0 |
| FollowUpChip | `voice/FollowUpChip.tsx` | ❌ | P0 |
| MacrosPanel | `voice/MacrosPanel.tsx` | ❌ | P1 |
| MeetingPanel | `voice/MeetingPanel.tsx` | ❌ | P1 |
| NotesPanel | `voice/NotesPanel.tsx` | ❌ | P1 |
| ProfileSwitcher | `voice/ProfileSwitcher.tsx` | ❌ | P1 |

### Takeover Mode (all P0 — core differentiator)
| Component | Web File | Desktop |
|---|---|:---:|
| TakeoverOrchestrator | `takeover/TakeoverOrchestrator.tsx` | ❌ |
| MatrixRain | `takeover/effects/MatrixRain.tsx` | ❌ |
| NoiseOverlay | `takeover/effects/NoiseOverlay.tsx` | ❌ |
| VignetteOverlay | `takeover/effects/VignetteOverlay.tsx` | ❌ |
| HudLayout | `takeover/hud/HudLayout.tsx` | ❌ |
| ControlLevel | `takeover/hud/ControlLevel.tsx` | ❌ |
| DirectivePanel | `takeover/hud/DirectivePanel.tsx` | ❌ |
| LeftPanel | `takeover/hud/LeftPanel.tsx` | ❌ |
| NeuralBrain | `takeover/hud/NeuralBrain.tsx` | ❌ |
| RightPanel | `takeover/hud/RightPanel.tsx` | ❌ |
| TerminalFeed | `takeover/hud/TerminalFeed.tsx` | ❌ |
| ActivationPhase | `takeover/phases/ActivationPhase.tsx` | ❌ |
| BreachPhase | `takeover/phases/BreachPhase.tsx` | ❌ |
| CommandPhase | `takeover/phases/CommandPhase.tsx` | ❌ |
| RootPhase | `takeover/phases/RootPhase.tsx` | ❌ |
| SyncPhase | `takeover/phases/SyncPhase.tsx` | ❌ |

### Ambient
| Component | Web File | Desktop | Priority |
|---|---|:---:|---|
| PassiveHUD | `ambient/PassiveHUD.tsx` | ❌ | P0 |
| ThoughtStream | `ambient/ThoughtStream.tsx` | ❌ | P1 |

### System
| Component | Web File | Desktop | Priority |
|---|---|:---:|---|
| EnvironmentPanel | `system/EnvironmentPanel.tsx` | ❌ | P1 |
| SystemInfoPanel | `system/SystemInfoPanel.tsx` | ❌ | P1 |
| ProactiveToast | `system/ProactiveToast.tsx` | ❌ | P0 |

### Command
| Component | Web File | Desktop | Priority |
|---|---|:---:|---|
| CommandBar | `command/CommandBar.tsx` | ❌ | P0 |
| CommandHistory | `command/CommandHistory.tsx` | ❌ | P0 |
| CommandResult | `command/CommandResult.tsx` | ❌ | P0 |
| DraftPreview | `command/DraftPreview.tsx` | ❌ | P0 |
| ExampleCommands | `command/ExampleCommands.tsx` | ❌ | P1 |

### Im-Home Protocol
| Component | Web File | Desktop | Priority |
|---|---|:---:|---|
| ImHomeProtocol | `im-home/ImHomeProtocol.tsx` | ❌ | P1 |
| CinematicOrb | `im-home/CinematicOrb.tsx` | ❌ | P1 |
| NeuralCanvas | `im-home/NeuralCanvas.tsx` | ❌ | P1 |

### UI Primitives
| Component | Web File | Desktop | Priority |
|---|---|:---:|---|
| Badge | `ui/Badge.tsx` | ❌ | P0 |
| Button | `ui/Button.tsx` | ❌ | P0 |
| Card | `ui/Card.tsx` | ❌ | P0 |
| EmptyState | `ui/EmptyState.tsx` | ❌ | P0 |
| LoadingSpinner | `ui/LoadingSpinner.tsx` | ❌ | P0 |
| StatusDot | `ui/StatusDot.tsx` | ❌ | P0 |
| Tooltip | `ui/Tooltip.tsx` | ❌ | P1 |
| VoicePlayer | `ui/VoicePlayer.tsx` | ❌ | P1 |

### Domain Components
| Component | Web File | Desktop | Priority |
|---|---|:---:|---|
| ApprovalCard | `approvals/ApprovalCard.tsx` | ❌ | P0 |
| ActivityItem | `activity/ActivityItem.tsx` | ❌ | P0 |
| IntegrationCard | `integrations/IntegrationCard.tsx` | ❌ | P1 |
| WorkflowItem | `workflows/WorkflowItem.tsx` | ❌ | P1 |
| TaskProgressPanel | `tasks/TaskProgressPanel.tsx` | ❌ | P0 |
| CodeAssistantPanel | `code/CodeAssistantPanel.tsx` | ❌ | P1 |

---

## Hooks — Web Parity

| Hook | Web | Desktop | Priority |
|---|:---:|:---:|---|
| useActivity | ✅ | ❌ | P0 |
| useApi | ✅ | ❌ | P0 |
| useApprovals | ✅ | ❌ | P0 |
| useAssistantSettings | ✅ | ⚠️ | P0 |
| useCognitiveState | ✅ | ❌ | P0 |
| useCommands | ✅ | ❌ | P0 |
| useDrafts | ✅ | ❌ | P0 |
| useEmotionState | ✅ | ❌ | P0 |
| useEnvironment | ✅ | ❌ | P1 |
| useHistory | ✅ | ⚠️ | P0 |
| useImHomeProtocol | ✅ | ❌ | P1 |
| useIntegrations | ✅ | ❌ | P1 |
| useMacros | ✅ | ⚠️ | P1 |
| useNotes | ✅ | ⚠️ | P1 |
| useParallaxCore | ✅ | ❌ | P2 |
| useProactive | ✅ | ⚠️ | P0 |
| useReminders | ✅ | ❌ | P1 |
| useSystemMetrics | ✅ | ❌ | P1 |
| useTakeoverMode | ✅ | ❌ | P0 |
| useTasks | ✅ | ❌ | P0 |
| useThoughtGenerator | ✅ | ❌ | P1 |
| useUIMode | ✅ | ❌ | P0 |
| useVoice | ✅ | ❌ | P0 |
| useVoiceSession | ✅ | ⚠️ | P0 |
| useWakeWord | ✅ | ✅ | — |
| useWorkflows | ✅ | ❌ | P1 |

---

## API Routes — Full Coverage Verification

| Endpoint | Web Uses | Desktop Must Have |
|---|:---:|:---:|
| GET `/api/v1/health` | ✅ | ✅ |
| GET `/api/v1/status` | ✅ | ✅ |
| GET `/api/v1/commands?limit=N` | ✅ | ✅ |
| POST `/api/v1/commands` | ✅ | ✅ |
| GET `/api/v1/commands/:id` | ✅ | ✅ |
| GET `/api/v1/approvals?status=X` | ✅ | ✅ |
| POST `/api/v1/approvals/:id/approve` | ✅ | ✅ |
| POST `/api/v1/approvals/:id/reject` | ✅ | ✅ |
| GET `/api/v1/activity` | ✅ | ✅ |
| GET `/api/v1/integrations` | ✅ | ✅ |
| GET `/api/v1/workflows` | ✅ | ✅ |
| GET `/api/v1/drafts` | ✅ | ✅ |
| POST `/api/v1/drafts/:id/confirm` | ✅ | ✅ |
| POST `/api/v1/drafts/:id/reject` | ✅ | ✅ |
| PATCH `/api/v1/drafts/:id` | ✅ | ✅ |
| POST `/api/v1/voice/transcribe` | ✅ | ✅ |
| GET `/api/v1/system/metrics` | ✅ | ✅ |
| GET `/api/v1/cognition/state` | ✅ | ✅ |
| WS `ws://localhost:8000/api/v1/voice/ws` | ✅ | ✅ |
| SSE `/api/v1/brain/emotion` | ✅ | ✅ |

---

## Desktop-Only Features (Tauri-Native)

These features do not exist in the web app and are desktop-exclusive:

| Feature | Implementation | Priority |
|---|---|---|
| System tray with show/hide | Tauri tray API (Rust) | P0 |
| Global push-to-talk shortcut | `tauri_plugin_global_shortcut` | P0 |
| Alt+Space show/hide toggle | `tauri_plugin_global_shortcut` | P0 |
| Native OS notifications | `tauri_plugin_notification` | P0 |
| Backend Python auto-start | Tauri shell plugin (Rust) | P0 |
| Window minimize/maximize/close | `@tauri-apps/api/window` | P0 |
| Always-on-top voice orb overlay | Tauri second window + always_on_top | P1 |
| Native window blur/vibrancy | Tauri `window-vibrancy` crate | P2 |
| Screenshot capture | `tauri_plugin_fs` + platform API | P1 |
| PowerShell/WSL2 automation bridge | Tauri shell plugin (Rust) | P1 |
| Clipboard read/write | `tauri_plugin_clipboard_manager` | P0 |
| Filesystem access (logs, data) | `tauri_plugin_fs` | P1 |
| Offline mode / local cache | `tauri_plugin_store` | P2 |
| Ollama process monitor | Tauri shell + sidecar | P2 |
| Resource monitor (CPU/RAM) | Tauri `sysinfo` crate | P2 |
| Auto-updater | `tauri_plugin_updater` | P2 |
| Crash recovery | Tauri watchdog / restart | P3 |
| GPU acceleration | WebView2 hardware acceleration (default on) | P1 |
| Startup launch | Platform autostart via Tauri | P3 |

---

## Takeover Mode — Desktop Enhancement

Desktop must enhance the web takeover experience with native features:

| Enhancement | Description |
|---|---|
| Red/orange glow | CSS `box-shadow` + `filter: drop-shadow` on window border (via `window-vibrancy`) |
| Window state change | Switch to always-on-top during takeover, restore after |
| Emergency stop | Global shortcut `Ctrl+Shift+Esc` → deactivate takeover |
| Audio cue | Play activation sound via Web Audio API |
| Native notification | Notify when takeover activates/deactivates |

---

## Voice Pipeline — Desktop Requirements

| Requirement | Implementation |
|---|---|
| Persistent WebSocket | `useWakeWord` hook → `ws://localhost:8000/api/v1/voice/ws` |
| Realtime streaming STT | `useVoiceSession` → Whisper via backend |
| VAD (Voice Activity Detection) | `@ricky0123/vad-react` in WebView |
| Interruptible TTS | AudioQueue in `voice-core.ts` |
| Wake word bridge | `useWakeWord` → WebSocket |
| Live waveform | `VoiceWave` component (already in desktop) |
| Audio device manager | Web Audio API `enumerateDevices()` |
| Voice latency monitor | Timestamp diff user-speech → first-audio-chunk |

---

## CSS / Theme Parity

| Requirement | Source | Status |
|---|---|:---:|
| Cyberpunk dark theme | `web/src/app/globals.css` | ❌ Copy to shared |
| UI mode overrides | `web/src/styles/ui-modes.css` | ❌ Copy to shared |
| `cyber-panel` class | `globals.css` | ❌ Copy |
| Tailwind tokens (brand, surface, bg, border, etc.) | `tailwind.config.ts` | ❌ Copy |
| Framer Motion 12 | `package.json` | ❌ Upgrade |

---

## Performance Verification Checklist

- [ ] RAM idle < 350 MB (measure with `htop` / Task Manager after startup)
- [ ] Cold start to interactive < 2 seconds
- [ ] Route switch < 100ms (React DevTools profiler)
- [ ] Wake-word trigger → audio response < 1200ms end-to-end
- [ ] Takeover activation → visual < 150ms
- [ ] All routes code-split with `React.lazy()`
- [ ] Long lists (activity, history) virtualized with `react-window` or `@tanstack/virtual`
- [ ] No polling hooks run when app is hidden/minimized
