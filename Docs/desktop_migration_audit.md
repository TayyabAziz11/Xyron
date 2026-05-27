# Desktop Migration Audit — Electron → Tauri
_Generated: 2026-05-18_

## Executive Summary

The current `desktop-app/` is an **Electron 36 + electron-vite** shell with 4 pages and ~10 source files. The web app has 9 full pages, 70+ components, 25+ hooks, a complete API layer, emotion/cognitive state system, full takeover-mode UI (12 files), and a live voice pipeline.

**Desktop parity coverage: ~18% of web surface area.**

Migration strategy: strip all Electron code, scaffold Tauri v2, import the entire web component tree as a shared package, add Tauri-native desktop-only features on top.

---

## 1. Current Desktop Inventory

### Pages (4 total)
| Page | File | Description |
|---|---|---|
| Home | `pages/Home.tsx` | Voice orb + chat bubble thread, wake-word indicator |
| Command Center | `pages/CommandCenter.tsx` | Command input + history list |
| Activity Timeline | `pages/ActivityTimeline.tsx` | Activity log |
| Settings | `pages/Settings.tsx` | Basic settings form |

### Components (4 total)
| Component | File |
|---|---|
| Sidebar | `components/Sidebar.tsx` |
| StatusBadge | `components/StatusBadge.tsx` |
| VoiceOrb | `components/VoiceOrb.tsx` |
| VoiceWave | `components/VoiceWave.tsx` |

### Hooks (7 total)
| Hook | File |
|---|---|
| useAssistantSettings | `hooks/useAssistantSettings.ts` |
| useHistory | `hooks/useHistory.ts` |
| useMacros | `hooks/useMacros.ts` |
| useNotes | `hooks/useNotes.ts` |
| useProactive | `hooks/useProactive.ts` |
| useVoiceSession | `hooks/useVoiceSession.ts` |
| useWakeWord | `hooks/useWakeWord.ts` |

### Electron-Specific Infrastructure
| File | Role | Migration Action |
|---|---|---|
| `src/main/index.ts` | Electron main process, IPC, tray, backend spawn | **DELETE** — replace with `src-tauri/` |
| `src/preload/index.ts` | contextBridge exposing `window.electronAPI` | **DELETE** — Tauri uses direct invocation |
| `electron.vite.config.ts` | electron-vite bundler config | **DELETE** — replace with Vite config |
| `package.json` build section | electron-builder packaging config | **REWRITE** — Tauri handles bundling |

### Electron Dependencies to Remove
```
electron, electron-builder, electron-vite
@electron-toolkit/utils, @electron-toolkit/tsconfig
```

### Electron IPC Calls Used in Renderer (all to be replaced)
All calls go through `window.electronAPI.*`:
- `submitCommand`, `getCommands`, `getCommand`
- `getActivity`, `getHealth`, `getIntegrations`, `getBackendStatus`
- `approveCommand`, `rejectCommand`
- `openUrl`, `copyText`, `launchApp`, `getSystemInfo`
- `minimize`, `maximize`, `close`
- `automateType`, `automateHotkey`, `takeScreenshot`

In Tauri: window controls → `@tauri-apps/api/window`; all API calls → direct `fetch` to `http://localhost:8000` (same as web); OS automation → Tauri shell plugin.

---

## 2. Current Web Inventory

### Pages (9 total)
| Route | File | Key Features |
|---|---|---|
| `/app` | `page.tsx` | Root redirect |
| `/app/dashboard` | `dashboard/page.tsx` | BrainPanel, ActivityFeed, ParticleCanvas, StatCards, QuickCommand |
| `/app/command-center` | `command-center/page.tsx` | Full voice session, takeover mode, im-home, ThoughtStream, 15+ hooks |
| `/app/activity` | `activity/page.tsx` | Activity timeline feed |
| `/app/approvals` | `approvals/page.tsx` | Approval cards, approve/reject actions |
| `/app/history` | `history/page.tsx` | Command history with search |
| `/app/integrations` | `integrations/page.tsx` | Integration grid cards |
| `/app/settings` | `settings/page.tsx` | Assistant settings, profiles |
| `/app/stats` | `stats/page.tsx` | Stats dashboard |
| `/app/workflows` | `workflows/page.tsx` | Workflow list |

### Components (72 total)

**Layout (4)**
- `AppShell.tsx` — root shell, sidebar + header + PassiveHUD + UI mode badge
- `Header.tsx` — top bar with mobile menu
- `Sidebar.tsx` — nav sidebar with routes
- `PageTransition.tsx` — route transition wrapper

**Dashboard (7)**
- `BrainCanvas.tsx`, `BrainPanel.tsx`, `ActivityFeed.tsx`
- `IntegrationGrid.tsx`, `ParticleCanvas.tsx`
- `QuickCommand.tsx`, `StatCard.tsx`

**Takeover (12)**
- `TakeoverOrchestrator.tsx`
- Effects: `MatrixRain.tsx`, `NoiseOverlay.tsx`, `VignetteOverlay.tsx`
- HUD: `HudLayout.tsx`, `ControlLevel.tsx`, `DirectivePanel.tsx`, `LeftPanel.tsx`, `NeuralBrain.tsx`, `RightPanel.tsx`, `TerminalFeed.tsx`
- Phases: `ActivationPhase.tsx`, `BreachPhase.tsx`, `CommandPhase.tsx`, `RootPhase.tsx`, `SyncPhase.tsx`

**Voice (7)**
- `ConversationThread.tsx`, `FollowUpChip.tsx`, `MacrosPanel.tsx`
- `MeetingPanel.tsx`, `NotesPanel.tsx`, `ProfileSwitcher.tsx`, `VoiceSessionPanel.tsx`

**Ambient (3)**
- `PassiveHUD.tsx`, `ThoughtStream.tsx`, `index.ts`

**System (3)**
- `EnvironmentPanel.tsx`, `ProactiveToast.tsx`, `SystemInfoPanel.tsx`

**Command (5)**
- `CommandBar.tsx`, `CommandHistory.tsx`, `CommandResult.tsx`
- `DraftPreview.tsx`, `ExampleCommands.tsx`

**Tasks (1)**
- `TaskProgressPanel.tsx`

**Code (1)**
- `CodeAssistantPanel.tsx`

**Im-Home (3)**
- `CinematicOrb.tsx`, `ImHomeProtocol.tsx`, `NeuralCanvas.tsx`

**UI Primitives (8)**
- `Badge.tsx`, `Button.tsx`, `Card.tsx`, `EmptyState.tsx`
- `LoadingSpinner.tsx`, `StatusDot.tsx`, `Tooltip.tsx`, `VoicePlayer.tsx`

**Approvals/Activity/Integrations/Workflows (4)**
- `ApprovalCard.tsx`, `ActivityItem.tsx`, `IntegrationCard.tsx`, `WorkflowItem.tsx`

### Hooks (25 total)
| Hook | Purpose |
|---|---|
| useActivity | Activity feed polling |
| useApi | Generic API wrapper |
| useApprovals | Approval queue management |
| useAssistantSettings | Profile settings r/w |
| useCognitiveState | Brain cognitive state SSE/poll |
| useCommands | Command list + submit |
| useDrafts | Draft management |
| useEmotionState | Emotion SSE from brain |
| useEnvironment | System environment info |
| useHistory | Command history |
| useImHomeProtocol | Im-home cinematic activation |
| useIntegrations | Integration list |
| useMacros | Voice macros |
| useNotes | Session notes |
| useParallaxCore | Mouse parallax effects |
| useProactive | Proactive suggestion toasts |
| useReminders | Reminder management |
| useSystemMetrics | CPU/RAM/disk metrics |
| useTakeoverMode | Takeover phase state machine |
| useTasks | Task progress |
| useThoughtGenerator | Ambient thought generation |
| useUIMode | UI mode (focus/calm/overdrive) |
| useVoice | Low-level voice control |
| useVoiceSession | Full voice session pipeline |
| useWakeWord | Wake-word WS connection |

### State & Config
| File | Purpose |
|---|---|
| `state/emotionState.ts` | ORB_VARIANTS color/glow/duration map |
| `contexts/UIModeContext.tsx` | UI mode React context |
| `config/emotions.ts` | Emotion configuration |
| `lib/api.ts` | Typed REST API client |
| `lib/types.ts` | All shared TypeScript types |
| `lib/utils.ts` | Utility helpers |
| `lib/voice-core.ts` | Core voice pipeline logic |
| `lib/im-home-audio.ts` | Im-home audio logic |
| `styles/ui-modes.css` | Per-mode CSS classes |
| `app/globals.css` | Global Tailwind styles |

---

## 3. Parity Matrix

| Feature | Web | Desktop | Gap |
|---|:---:|:---:|---|
| **PAGES** | | | |
| Dashboard (Brain panels, stats) | ✅ | ❌ | Missing |
| Command Center (full) | ✅ | ⚠️ | Partial — no takeover, no im-home, no 12 voice components |
| Activity timeline | ✅ | ⚠️ | Partial — basic list, no ActivityItem component |
| Approvals | ✅ | ❌ | Missing |
| History | ✅ | ❌ | Missing |
| Integrations | ✅ | ❌ | Missing |
| Settings | ✅ | ⚠️ | Partial — basic, missing ProfileSwitcher |
| Stats | ✅ | ❌ | Missing |
| Workflows | ✅ | ❌ | Missing |
| **LAYOUT** | | | |
| AppShell with PassiveHUD | ✅ | ❌ | Desktop uses bare Sidebar |
| Header with mobile menu | ✅ | ❌ | Missing |
| PageTransition animations | ✅ | ⚠️ | Custom motion wrapper, not shared |
| UI mode badge + context | ✅ | ❌ | Missing |
| **VOICE PIPELINE** | | | |
| Voice session (full) | ✅ | ⚠️ | Desktop has useVoiceSession but older version |
| Wake-word bridge | ✅ | ✅ | Present in both |
| ConversationThread UI | ✅ | ❌ | Desktop shows raw chat bubbles inline |
| VoiceSessionPanel | ✅ | ❌ | Missing |
| MacrosPanel | ✅ | ❌ | Missing |
| MeetingPanel | ✅ | ❌ | Missing |
| NotesPanel | ✅ | ❌ | Missing |
| FollowUpChip | ✅ | ❌ | Missing |
| ProfileSwitcher | ✅ | ❌ | Missing |
| VoicePlayer | ✅ | ❌ | Missing |
| **TAKEOVER MODE** | | | |
| useTakeoverMode hook | ✅ | ❌ | Missing |
| TakeoverOrchestrator | ✅ | ❌ | Missing |
| MatrixRain effect | ✅ | ❌ | Missing |
| VignetteOverlay | ✅ | ❌ | Missing |
| NoiseOverlay | ✅ | ❌ | Missing |
| 5 phase components | ✅ | ❌ | Missing |
| 7 HUD components | ✅ | ❌ | Missing |
| **BRAIN / COGNITION** | | | |
| BrainPanel | ✅ | ❌ | Missing |
| BrainCanvas | ✅ | ❌ | Missing |
| useCognitiveState | ✅ | ❌ | Missing |
| useEmotionState | ✅ | ❌ | Missing |
| emotionState (ORB_VARIANTS) | ✅ | ❌ | Missing |
| **AMBIENT / HUD** | | | |
| PassiveHUD | ✅ | ❌ | Missing |
| ThoughtStream | ✅ | ❌ | Missing |
| useThoughtGenerator | ✅ | ❌ | Missing |
| Im-Home protocol | ✅ | ❌ | Missing |
| CinematicOrb | ✅ | ❌ | Missing |
| NeuralCanvas | ✅ | ❌ | Missing |
| **SYSTEM / ENV** | | | |
| EnvironmentPanel | ✅ | ❌ | Missing |
| SystemInfoPanel | ✅ | ❌ | Missing |
| ProactiveToast | ✅ | ❌ | Missing |
| useSystemMetrics | ✅ | ❌ | Missing |
| useEnvironment | ✅ | ❌ | Missing |
| **COMMAND LAYER** | | | |
| CommandBar | ✅ | ❌ | Missing |
| CommandHistory | ✅ | ❌ | Missing |
| CommandResult | ✅ | ❌ | Missing |
| DraftPreview | ✅ | ❌ | Missing |
| ExampleCommands | ✅ | ❌ | Missing |
| useDrafts | ✅ | ❌ | Missing |
| useCommands | ✅ | ⚠️ | Hook exists but uses IPC |
| **UI PRIMITIVES** | | | |
| Badge, Button, Card | ✅ | ❌ | Missing shared components |
| EmptyState, LoadingSpinner | ✅ | ❌ | Missing |
| StatusDot, Tooltip | ✅ | ❌ | Missing |
| **API LAYER** | | | |
| Typed api.ts client | ✅ | ❌ | Desktop uses IPC bridge |
| lib/types.ts | ✅ | ❌ | No shared types |
| lib/utils.ts | ✅ | ❌ | No shared utils |
| voice-core.ts | ✅ | ❌ | Missing |
| **ROUTING** | | | |
| 9 routes | ✅ | ❌ | 4 routes |
| **WEBSOCKET / SSE** | | | |
| Voice WS (voice_ws endpoint) | ✅ | ✅ | Both have useWakeWord |
| Cognition state poll | ✅ | ❌ | Missing |
| Emotion state SSE | ✅ | ❌ | Missing |
| **HOTKEYS** | | | |
| 1-4 keyboard nav | ✅ | ✅ | Both have it |
| Global push-to-talk | ❌ | ✅ | Desktop only (via globalShortcut) → Tauri |
| Alt+Space show/hide | ❌ | ✅ | Desktop only → Tauri |
| **DESKTOP-NATIVE** | | | |
| System tray | ❌ | ✅ | Electron → needs Tauri equivalent |
| Backend auto-start | ❌ | ✅ | Electron → needs Tauri shell |
| Screenshot capture | ❌ | ✅ | Electron → needs Tauri |
| PowerShell bridge | ❌ | ✅ | Electron → needs Tauri shell |
| Native notifications | ❌ | ✅ | Electron → needs Tauri notification |
| Window controls | ❌ | ✅ | Electron → needs Tauri window |
| Always-on-top | ❌ | ❌ | Not implemented anywhere |
| Native window blur | ❌ | ❌ | Not implemented anywhere |
| Ollama monitor | ❌ | ❌ | Not implemented anywhere |
| Resource monitor | ❌ | ❌ | Not implemented anywhere |
| Auto-updater | ❌ | ❌ | Not implemented anywhere |

**Gap summary: 82% of web surface area is missing from desktop.**

---

## 4. Dead / Stale Desktop Code

| File | Issue |
|---|---|
| `src/main/index.ts` | Entire file is Electron-specific — delete |
| `src/preload/index.ts` | contextBridge pattern — delete |
| `electron.vite.config.ts` | Electron-vite config — delete |
| All `window.electronAPI.*` calls | IPC pattern — replace with direct fetch |
| `ipcRenderer.invoke` in hooks | IPC pattern — replace with fetch |
| `cross-env` dev dep | Not needed in Tauri |
| `@electron-toolkit/*` deps | Electron-only — remove |

---

## 5. CSS / Style Gap

| Item | Web | Desktop |
|---|:---:|:---:|
| `globals.css` with cyber/cyberpunk classes | ✅ | ❌ |
| `ui-modes.css` per-mode overrides | ✅ | ❌ |
| `cyber-panel` CSS class | ✅ | ❌ |
| Tailwind color tokens (brand, surface, bg, etc.) | ✅ | ⚠️ |
| Framer Motion 12 | ✅ | ❌ (has v11) |

---

## 6. Dependency Delta

| Package | Web | Desktop | Action |
|---|:---:|:---:|---|
| `react` | 19.0.0 | 18.3.1 | Upgrade desktop to 19 |
| `framer-motion` | 12.x | 11.x | Upgrade desktop to 12 |
| `lucide-react` | 0.511.x | 0.400.x | Upgrade |
| `next` | 15.x | N/A | Not needed in Tauri |
| `react-router-dom` | N/A | 6.26.x | Keep for Tauri SPA routing |
| `@ricky0123/vad-react` | ✅ | ❌ | Add to desktop |
| `@ricky0123/vad-web` | ✅ | ❌ | Add to desktop |
| `clsx` | ✅ | ❌ | Add to desktop |
| `tailwind-merge` | ✅ | ❌ | Add to desktop |
| `zustand` | ❌ | ❌ | Add to both (required by migration plan) |
| `@tanstack/react-query` | ❌ | ❌ | Add to desktop |
| `@tauri-apps/api` | ❌ | ❌ | Add to desktop |
| `@tauri-apps/plugin-*` | ❌ | ❌ | Add to desktop |
| `electron` | N/A | 36.x | **REMOVE** |
| `electron-builder` | N/A | 25.x | **REMOVE** |
| `electron-vite` | N/A | 2.3.x | **REMOVE** |
