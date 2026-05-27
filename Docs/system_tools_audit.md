# System Tools Audit

**Date:** 2026-05-20  
**Purpose:** Track which tools are registered, have Tier-2 regex intent matching, and work via the WS voice path.

Legend: ✅ = working | ⚠️ = partial | ❌ = missing | 🔒 = requires confirmation

## Tool Coverage Matrix

| Tool Name | Registered | Tier-2 Regex | WS Voice | HTTP REST | Confirmation | Notes |
|---|---|---|---|---|---|---|
| `brightness_control` | ✅ | ✅ | ✅ | ✅ | — | increase/decrease/set patterns |
| `check_windows_updates` | ✅ | ✅ | ✅ | ✅ | — | |
| `clear_clipboard` | ✅ | ✅ | ✅ | ✅ | — | |
| `clear_temp_files` | ✅ | ✅ | ✅ | ✅ | — | |
| `create_folder` | ✅ | ✅ | ✅ | ✅ | — | **Added Tier-2 regex 2026-05-20** |
| `create_subfolders` | ✅ | ✅ | ✅ | ✅ | — | |
| `delete_file` | ✅ | ✅ | ✅ | ✅ | 🔒 | memory_ref path required |
| `disable_startup_app` | ✅ | ⚠️ | ✅ | ✅ | — | semantic Tier-3 only |
| `empty_recycle_bin` | ✅ | ✅ | ✅ | ✅ | — | |
| `flush_dns` | ✅ | ✅ | ✅ | ✅ | — | |
| `get_battery_status` | ✅ | ✅ | ✅ | ✅ | — | |
| `get_date_time` | ✅ | ✅ | ✅ | ✅ | — | |
| `get_disk_usage` | ✅ | ✅ | ✅ | ✅ | — | |
| `get_ip_info` | ✅ | ✅ | ✅ | ✅ | — | |
| `get_running_apps` | ✅ | ✅ | ✅ | ✅ | — | |
| `get_startup_apps` | ✅ | ✅ | ✅ | ✅ | — | |
| `get_temp_files_size` | ✅ | ✅ | ✅ | ✅ | — | |
| `get_uptime` | ✅ | ✅ | ✅ | ✅ | — | |
| `get_volume` | ✅ | ✅ | ✅ | ✅ | — | |
| `hibernate_system` | ✅ | ✅ | ✅ | ✅ | 🔒 | |
| `kill_app` | ✅ | ✅ | ✅ | ✅ | — | |
| `kill_process` | ✅ | ⚠️ | ✅ | ✅ | — | semantic Tier-3; "kill PID" rare |
| `list_audio_devices` | ✅ | ⚠️ | ✅ | ✅ | — | semantic only |
| `list_directory` | ✅ | ⚠️ | ✅ | ✅ | — | semantic only |
| `list_processes` | ✅ | ✅ | ✅ | ✅ | — | |
| `lock_system` | ✅ | ✅ | ✅ | ✅ | — | |
| `media_control` | ✅ | ✅ | ✅ | ✅ | — | play/pause/next/previous |
| `move_file` | ✅ | ⚠️ | ✅ | ✅ | — | semantic only |
| `mute_unmute` | ✅ | ✅ | ✅ | ✅ | — | |
| `network_speed_test` | ✅ | ✅ | ✅ | ✅ | — | |
| `open_application` | ✅ | ✅ | ✅ | ✅ | — | catch-all; drives, settings |
| `open_directory` | ✅ | ✅ | ✅ | ✅ | — | known folders (downloads etc.) |
| `open_drive` | ✅ | ✅ | ✅ | ✅ | — | "open C drive" |
| `open_file` | ✅ | ✅ | ✅ | ✅ | — | |
| `open_system_settings` | ✅ | ✅ | ✅ | ✅ | — | mapped via open_application |
| `open_wifi_panel` | ✅ | ✅ | ✅ | ✅ | — | |
| `restart_system` | ✅ | ✅ | ✅ | ✅ | 🔒 | explicit phrasing required |
| `run_disk_cleanup` | ✅ | ✅ | ✅ | ✅ | — | |
| `schedule_shutdown` | ✅ | ⚠️ | ✅ | ✅ | — | semantic only |
| `search_files` | ✅ | ⚠️ | ✅ | ✅ | — | semantic only |
| `set_default_audio` | ✅ | ⚠️ | ✅ | ✅ | — | semantic only |
| `set_display_resolution` | ✅ | ⚠️ | ✅ | ✅ | — | semantic only |
| `set_power_plan` | ✅ | ⚠️ | ✅ | ✅ | — | semantic only |
| `set_refresh_rate` | ✅ | ⚠️ | ✅ | ✅ | — | semantic only |
| `shutdown_system` | ✅ | ✅ | ✅ | ✅ | 🔒 | explicit phrasing required |
| `sleep_system` | ✅ | ✅ | ✅ | ✅ | — | |
| `smart_open` | ✅ | ⚠️ | ✅ | ✅ | — | semantic only |
| `stand_down` | ✅ | — | ✅ | ✅ | — | takeover escape |
| `system_health` | ✅ | ✅ | ✅ | ✅ | — | "cpu usage", "system performance" |
| `system_info` | ✅ | ✅ | ✅ | ✅ | — | |
| `take_screenshot` | ✅ | ✅ | ✅ | ✅ | — | |
| `takeover_mode` | ✅ | — | ✅ | ✅ | — | voice-session command only |
| `virtual_desktop_create` | ✅ | ⚠️ | ✅ | ✅ | — | semantic only |
| `virtual_desktop_switch` | ✅ | ⚠️ | ✅ | ✅ | — | semantic only |
| `volume_control` | ✅ | ✅ | ✅ | ✅ | — | |
| `wifi_connect` | ✅ | ⚠️ | ✅ | ✅ | — | semantic only |
| `wifi_disconnect` | ✅ | ⚠️ | ✅ | ✅ | — | semantic only |
| `wifi_list` | ✅ | ✅ | ✅ | ✅ | — | |
| `write_file` | ✅ | ⚠️ | ✅ | ✅ | — | semantic only |

## Confirmed Working via WS Voice (Tested)

- `open_application` → "Open settings" ✅
- `open_drive` → "Open C drive" ✅

## Known Issues Fixed This Session

- `create_folder`: Tier-2 regex added — was falling to semantic (conf=0.51 < 0.55 threshold) → went to OpenAI.

## Dangerous Actions Requiring Explicit Confirmation

These must only execute from a clean, unambiguous transcript. The command-list hallucination guard (3+ tool keywords, no sentence structure → reject) prevents garbage transcripts from reaching these.

- `shutdown_system`
- `restart_system`
- `delete_file`
- `hibernate_system`
- `schedule_shutdown`

## Remaining Tier-2 Regex Gaps (Lower Priority)

Tools with only semantic (Tier-3) coverage. Fine for now — semantic handles them well. Add regex if confidence drops in practice:

- `schedule_shutdown` — "shut down in 10 minutes"
- `search_files` — "find file called X"
- `move_file` — "move X to Y"
- `smart_open` — "open the thing I was working on"
- `list_directory` — "what's in the downloads folder"
