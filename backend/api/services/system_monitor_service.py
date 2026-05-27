"""
Real-time system monitor service.

Architecture
────────────
  Running inside WSL2: psutil reads the Linux VM — NOT the Windows host.
  Only battery, net byte-counters, and process count come from psutil.
  CPU %, RAM, and disk are queried from Windows via PowerShell.
  (wmic is removed in Windows 11 22H2+, so we use Get-Counter / CimInstance)

  Threads:
    sysmon-winfast  — Get-Counter CPU (1s sample) + CimInstance RAM every 2 s
    sysmon-slow     — GPU (nvidia-smi → all-engine counter) + disk every 10 s
    sysmon-static   — one-shot: CPU name, core count, OS, hostname, boot time
    sysmon-loop     — 1 Hz tick: merges cached Windows values with psutil
                      battery/net/uptime, then pushes to WS subscribers

  WebSocket clients → /api/v1/monitor/ws    (live frames, ~1 s cadence)
  REST clients      → /api/v1/monitor/snapshot  (voice / one-shot)

Log prefixes:
  [SYSMON_WINDOWS_CPU]    — CPU % source and value
  [SYSMON_WINDOWS_GPU]    — GPU % source and value
  [SYSMON_WINDOWS_UPTIME] — uptime from Windows LastBootUpTime
  [SYSMON_SOURCE]         — which data source was used for each metric

Thread safety: all shared state protected by threading.Lock.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import subprocess
import threading
import time
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

GiB = 1024 ** 3
MiB = 1024 ** 2
KiB = 1024

HISTORY_LEN       = 60   # sparkline history depth (seconds)
WIN_FAST_TTL      = 5.0  # treat cached Windows sample stale after 5 s
WIN_FAST_INTERVAL = 2.0  # re-query Windows every 2 s

_PS = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]


def _ps(cmd: str, timeout: float = 8.0) -> Optional[str]:
    """Run a PowerShell command; return stdout stripped, or None on error."""
    try:
        r = subprocess.run(_PS + [cmd], capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception as exc:
        logger.debug("[SystemMonitor] PS error: %s", exc)
        return None


# ── GPU helpers ────────────────────────────────────────────────────────────────

def _gpu_via_powershell() -> Dict[str, Any]:
    """
    GPU metrics with two-tier fallback:
      1. nvidia-smi — most accurate for NVIDIA; includes VRAM used
      2. Get-Counter all engines — works for AMD/Intel/NVIDIA without SDK
    Returns dict with gpu_pct=None if no GPU is detected.
    """
    # ── Tier 1: nvidia-smi ────────────────────────────────────────────────────
    smi_out = _ps(
        "try {"
        "  $r = & nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,name"
        "    --format=csv,noheader,nounits 2>$null;"
        "  if ($LASTEXITCODE -eq 0 -and $r) { $r } else { '' }"
        "} catch { '' }",
        timeout=6,
    )
    if smi_out and smi_out.strip():
        parts = [p.strip() for p in smi_out.strip().split(',')]
        if len(parts) >= 4:
            try:
                gpu_pct      = min(float(parts[0]), 100.0)
                vram_used_mb = float(parts[1])
                vram_total_mb = float(parts[2])
                gpu_name     = parts[3].strip()
                logger.info(
                    "[SYSMON_WINDOWS_GPU] source=nvidia-smi pct=%.1f%% "
                    "vram_used=%.0fMB vram_total=%.0fMB name=%s",
                    gpu_pct, vram_used_mb, vram_total_mb, gpu_name,
                )
                return {
                    "gpu_pct":       round(gpu_pct, 1),
                    "gpu_name":      gpu_name,
                    "vram_total_mb": round(vram_total_mb, 0),
                    "vram_used_mb":  round(vram_used_mb, 0),
                }
            except (ValueError, IndexError):
                pass

    # ── Tier 2: Windows perf counter — ALL GPU engines (not just engtype_3D) ──
    # Task Manager aggregates all engine types; engtype_3D alone misses
    # compute, copy, video-decode, etc. and reports 0% under normal load.
    gpu_pct: Optional[float] = None
    out = _ps(
        "try {"
        "  $s = (Get-Counter '\\GPU Engine(*)\\Utilization Percentage'"
        "    -SampleInterval 1 -MaxSamples 1 -ErrorAction SilentlyContinue).CounterSamples;"
        "  ($s | Measure-Object -Property CookedValue -Sum).Sum"
        "} catch { '' }",
        timeout=8,
    )
    if out and out.strip():
        try:
            v = float(out.strip())
            gpu_pct = min(round(v, 1), 100.0)
        except ValueError:
            pass

    # GPU name + VRAM total via CIM
    name: Optional[str] = None
    vram_total_mb: Optional[float] = None
    out2 = _ps(
        "Get-CimInstance Win32_VideoController | Select-Object -First 1 Name,AdapterRAM |"
        " ForEach-Object { \"NAME=$($_.Name) VRAM=$($_.AdapterRAM)\" }",
        timeout=5,
    )
    if out2:
        for token in out2.split():
            if token.startswith("NAME="):
                name = token[5:].strip() or None
            elif token.startswith("VRAM="):
                try:
                    vram_total_mb = int(token[5:]) / MiB
                except (ValueError, OverflowError):
                    pass

    if gpu_pct is not None or name:
        logger.info(
            "[SYSMON_WINDOWS_GPU] source=win_counter pct=%s name=%s vram_total_mb=%s",
            gpu_pct, name, vram_total_mb,
        )
    else:
        logger.info("[SYSMON_WINDOWS_GPU] source=none — no GPU detected or counters unavailable")

    return {
        "gpu_pct":       gpu_pct,
        "gpu_name":      name,
        "vram_total_mb": round(vram_total_mb, 0) if vram_total_mb else None,
        "vram_used_mb":  None,
    }


# ── Network speed calculator ───────────────────────────────────────────────────

class _NetSpeed:
    def __init__(self) -> None:
        self._last_sent: Optional[int] = None
        self._last_recv: Optional[int] = None
        self._last_t:    Optional[float] = None

    def update(self, sent: int, recv: int) -> tuple[float, float]:
        now = time.monotonic()
        if self._last_t is None:
            self._last_sent, self._last_recv, self._last_t = sent, recv, now
            return 0.0, 0.0
        dt = now - self._last_t
        if dt < 0.1:
            return 0.0, 0.0
        up   = max(0, (sent - self._last_sent) / dt / KiB)
        down = max(0, (recv - self._last_recv) / dt / KiB)
        self._last_sent, self._last_recv, self._last_t = sent, recv, now
        return round(up, 1), round(down, 1)


# ── Main service class ─────────────────────────────────────────────────────────

class SystemMonitorService:
    def __init__(self) -> None:
        self._lock    = threading.Lock()
        self._running = False

        self._history: Dict[str, Deque[float]] = {
            k: collections.deque(maxlen=HISTORY_LEN)
            for k in ("cpu", "ram", "gpu", "net_up", "net_down")
        }

        self._snap:   Dict[str, Any] = {}
        self._static: Dict[str, Any] = {}
        self._slow:   Dict[str, Any] = {}

        # Windows-host fast cache — refreshed every WIN_FAST_INTERVAL seconds
        self._win_fast: Dict[str, Any] = {}
        self._last_win_fast_refresh = 0.0
        self._last_slow_refresh     = 0.0

        # Windows boot time as Unix timestamp — populated once by _collect_static
        self._win_boot_unix: Optional[float] = None

        self._net_speed = _NetSpeed()
        self._subscribers: list[asyncio.Queue] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── Subscription API ───────────────────────────────────────────────────────

    def subscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.append(q)

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    # ── Read API ───────────────────────────────────────────────────────────────

    def get_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {**self._snap, **self._static}

    def get_history(self, metric: str, n: int = 60) -> List[float]:
        with self._lock:
            dq = self._history.get(metric, collections.deque())
            return list(dq)[-n:]

    def get_full_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            snap = {**self._snap, **self._static}
            snap["history"] = {k: list(v) for k, v in self._history.items()}
            return snap

    # ── Startup / shutdown ────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._running:
            return
        self._running = True
        self._loop    = loop
        threading.Thread(target=self._collect_static, daemon=True,
                         name="sysmon-static").start()
        threading.Thread(target=self._run_loop, daemon=True,
                         name="sysmon-loop").start()
        logger.info("[SystemMonitor] started — win_fast=2s slow=10s history=%ds", HISTORY_LEN)

    def stop(self) -> None:
        self._running = False

    # ── Static info (one-time) ────────────────────────────────────────────────

    def _collect_static(self) -> None:
        try:
            import psutil, platform
            static: Dict[str, Any] = {}
            static["cpu_name"]           = self._get_cpu_name()
            static["cpu_physical_cores"] = psutil.cpu_count(logical=False) or 0
            static["cpu_logical_cores"]  = psutil.cpu_count(logical=True)  or 0
            # Get RAM total from Windows (not the WSL2 VM's smaller view)
            static["ram_total_gb"] = self._get_win_ram_total_gb() \
                                     or round(psutil.virtual_memory().total / GiB, 1)
            static["os_platform"] = platform.system()
            try:
                static["hostname"] = platform.node()
            except Exception:
                static["hostname"] = "unknown"

            # Windows boot time → Unix timestamp for accurate uptime calculation
            win_boot = self._get_win_boot_unix()
            if win_boot:
                with self._lock:
                    self._win_boot_unix = win_boot
                uptime_s = int(time.time() - win_boot)
                h, rem = divmod(uptime_s, 3600)
                m, s   = divmod(rem, 60)
                logger.info("[SYSMON_WINDOWS_UPTIME] boot_unix=%.0f uptime=%dh%02dm%02ds",
                            win_boot, h, m, s)

            with self._lock:
                self._static = static
            logger.info("[SystemMonitor] static: cpu=%s ram=%.1fGB",
                        static.get("cpu_name", "?"), static.get("ram_total_gb", 0))
        except Exception as exc:
            logger.warning("[SystemMonitor] static collection failed: %s", exc)

    def _get_cpu_name(self) -> str:
        out = _ps("(Get-CimInstance -ClassName Win32_Processor).Name", timeout=5)
        if out:
            return out.strip()
        try:
            import platform
            return platform.processor() or "Unknown CPU"
        except Exception:
            return "Unknown CPU"

    def _get_win_ram_total_gb(self) -> Optional[float]:
        out = _ps("(Get-CimInstance -ClassName Win32_OperatingSystem).TotalVisibleMemorySize",
                  timeout=5)
        if out:
            try:
                return round(int(out) / (1024 * 1024), 1)  # KB → GB
            except ValueError:
                pass
        return None

    def _get_win_boot_unix(self) -> Optional[float]:
        """
        Get Windows boot time as Unix epoch seconds.
        Uses LastBootUpTime converted to UTC epoch via PowerShell.
        """
        out = _ps(
            "[int64]("
            "  (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime()"
            "  - [datetime]'1970-01-01'"
            ").TotalSeconds",
            timeout=6,
        )
        if out and out.strip():
            try:
                return float(out.strip())
            except ValueError:
                pass
        return None

    # ── Windows fast metrics (CPU %, RAM) — every 2 s ─────────────────────────

    def _collect_windows_fast(self) -> None:
        """
        Get Windows-host CPU % (via Get-Counter, 1s sample — matches Task Manager)
        and RAM (via CimInstance). Results cached; stale after WIN_FAST_TTL seconds.

        Get-Counter samples over a 1-second window, which is why this takes ~1s.
        CimInstance RAM is appended to the same call for efficiency.
        """
        cmd = (
            "$cpu = [math]::Round("
            "  (Get-Counter '\\Processor(_Total)\\% Processor Time'"
            "    -SampleInterval 1 -MaxSamples 1 -ErrorAction SilentlyContinue"
            "  ).CounterSamples[0].CookedValue, 1);"
            "$os = Get-CimInstance Win32_OperatingSystem;"
            "\"CPU=$cpu TOTAL=$($os.TotalVisibleMemorySize) FREE=$($os.FreePhysicalMemory)\""
        )
        out = _ps(cmd, timeout=10)
        if not out:
            return

        data: Dict[str, str] = {}
        for token in out.split():
            if "=" in token:
                k, _, v = token.partition("=")
                data[k.strip()] = v.strip()

        win: Dict[str, Any] = {"_ts": time.time()}

        if "CPU" in data and data["CPU"]:
            try:
                cpu_val = float(data["CPU"])
                win["cpu_pct"] = round(cpu_val, 1)
                logger.info("[SYSMON_WINDOWS_CPU] source=Get-Counter pct=%.1f%%", cpu_val)
            except ValueError:
                pass

        if "TOTAL" in data and "FREE" in data:
            try:
                total_kb = int(data["TOTAL"])
                free_kb  = int(data["FREE"])
                used_kb  = total_kb - free_kb
                win["ram_total_gb"] = round(total_kb / (1024 * 1024), 1)
                win["ram_used_gb"]  = round(used_kb  / (1024 * 1024), 1)
                win["ram_free_gb"]  = round(free_kb  / (1024 * 1024), 1)
                win["ram_pct"]      = round(used_kb / total_kb * 100, 1) if total_kb else 0.0
            except (ValueError, ZeroDivisionError):
                pass

        with self._lock:
            self._win_fast.update(win)
            if "ram_total_gb" in win:
                self._static["ram_total_gb"] = win["ram_total_gb"]

        logger.debug(
            "[SYSMON_SOURCE] win_fast OK — cpu=%.1f%% ram=%.1f%%",
            win.get("cpu_pct", 0), win.get("ram_pct", 0),
        )

    # ── Slow metrics (GPU, disk, temp) — every 10 s ───────────────────────────

    def _collect_slow(self) -> None:
        slow: Dict[str, Any] = {}

        # GPU
        gpu = _gpu_via_powershell()
        if gpu:
            slow.update(gpu)
        else:
            slow.update({"gpu_pct": None, "gpu_name": None,
                         "vram_total_mb": None, "vram_used_mb": None})

        # Windows disk (fixed drives, C: D: etc.) via CimInstance
        try:
            out = _ps(
                "Get-CimInstance -ClassName Win32_LogicalDisk -Filter 'DriveType=3' |"
                " ForEach-Object { \"SIZE=$($_.Size) FREE=$($_.FreeSpace)\" }",
                timeout=8,
            )
            if out:
                disk_total_gb = disk_used_gb = 0.0
                for line in out.splitlines():
                    parts = {}
                    for token in line.split():
                        if "=" in token:
                            k, _, v = token.partition("=")
                            parts[k] = v
                    try:
                        sz   = int(parts.get("SIZE", "0") or "0")
                        free = int(parts.get("FREE", "0") or "0")
                        disk_total_gb += sz / GiB
                        disk_used_gb  += (sz - free) / GiB
                    except (ValueError, OverflowError):
                        pass
                if disk_total_gb > 0:
                    slow["disk_total_gb"] = round(disk_total_gb, 1)
                    slow["disk_used_gb"]  = round(disk_used_gb,  1)
                    slow["disk_free_gb"]  = round(disk_total_gb - disk_used_gb, 1)
                    slow["disk_pct"]      = round(disk_used_gb / disk_total_gb * 100, 1)
        except Exception as exc:
            logger.debug("[SystemMonitor] disk query failed: %s", exc)

        # CPU temperature (Linux sensors — likely empty in WSL2)
        slow["temp_c"] = None
        try:
            import psutil
            temps = psutil.sensors_temperatures()
            if temps:
                for key in ("coretemp", "cpu_thermal", "k10temp", "acpitz"):
                    if key in temps and temps[key]:
                        slow["temp_c"] = round(temps[key][0].current, 1)
                        break
        except (AttributeError, NotImplementedError):
            pass

        with self._lock:
            self._slow = slow

    # ── Main sample loop (1 Hz) ───────────────────────────────────────────────

    def _run_loop(self) -> None:
        try:
            import psutil
        except ImportError:
            logger.error("[SystemMonitor] psutil not installed — cannot run")
            return

        psutil.cpu_percent(interval=None)  # prime accumulator
        time.sleep(1.0)

        while self._running:
            t0 = time.monotonic()
            try:
                self._sample(psutil)
            except Exception as exc:
                logger.warning("[SystemMonitor] sample error: %s", exc)
            elapsed = time.monotonic() - t0
            time.sleep(max(0.0, 1.0 - elapsed))

    def _sample(self, psutil: Any) -> None:
        now = time.time()

        # Trigger background refreshes
        if now - self._last_win_fast_refresh >= WIN_FAST_INTERVAL:
            self._last_win_fast_refresh = now
            threading.Thread(target=self._collect_windows_fast, daemon=True,
                             name="sysmon-winfast").start()
        if now - self._last_slow_refresh >= 10.0:
            self._last_slow_refresh = now
            threading.Thread(target=self._collect_slow, daemon=True,
                             name="sysmon-slow").start()

        # Read cached Windows host values
        with self._lock:
            win = dict(self._win_fast)

        win_fresh = (now - win.get("_ts", 0)) < WIN_FAST_TTL

        # CPU
        if win_fresh and "cpu_pct" in win:
            cpu_pct = win["cpu_pct"]
            logger.debug("[SYSMON_SOURCE] cpu=win_counter pct=%.1f%%", cpu_pct)
        else:
            cpu_pct = round(psutil.cpu_percent(interval=None), 1)
            logger.debug("[SYSMON_SOURCE] cpu=psutil(WSL-fallback) pct=%.1f%%", cpu_pct)

        cpu_freq     = psutil.cpu_freq()
        cpu_freq_mhz = round(cpu_freq.current, 0) if cpu_freq else None

        # RAM
        if win_fresh and "ram_pct" in win:
            ram_pct     = win["ram_pct"]
            ram_used_gb = win["ram_used_gb"]
            ram_free_gb = win["ram_free_gb"]
        else:
            vm          = psutil.virtual_memory()
            ram_pct     = round(vm.percent, 1)
            ram_used_gb = round(vm.used / GiB, 1)
            ram_free_gb = round(vm.available / GiB, 1)

        try:
            swap_pct = round(psutil.swap_memory().percent, 1)
        except Exception:
            swap_pct = 0.0

        # Network (psutil byte counters — fine in WSL2)
        net = psutil.net_io_counters()
        net_up_kbs, net_down_kbs = self._net_speed.update(net.bytes_sent, net.bytes_recv)

        # Battery (passes through ACPI — accurate in WSL2)
        batt          = psutil.sensors_battery()
        batt_pct      = round(batt.percent, 1) if batt else None
        batt_charging = batt.power_plugged      if batt else None

        try:
            proc_count = len(psutil.pids())
        except Exception:
            proc_count = 0

        # Uptime: prefer Windows LastBootUpTime (accurate from host perspective)
        # _win_boot_unix is set once by _collect_static; falls back to WSL psutil
        with self._lock:
            _win_boot = self._win_boot_unix
        if _win_boot:
            uptime_s = int(now - _win_boot)
            logger.debug("[SYSMON_WINDOWS_UPTIME] source=Windows boot_unix=%.0f uptime_s=%d",
                         _win_boot, uptime_s)
        else:
            uptime_s = int(now - psutil.boot_time())
            logger.debug("[SYSMON_WINDOWS_UPTIME] source=psutil(WSL) uptime_s=%d", uptime_s)
        h, rem     = divmod(uptime_s, 3600)
        m, s       = divmod(rem, 60)
        uptime_str = f"{h}h {m:02d}m {s:02d}s"

        now_ms = int(time.time() * 1000)

        with self._lock:
            self._history["cpu"].append(cpu_pct)
            self._history["ram"].append(ram_pct)
            if self._slow.get("gpu_pct") is not None:
                self._history["gpu"].append(self._slow["gpu_pct"])
            self._history["net_up"].append(net_up_kbs)
            self._history["net_down"].append(net_down_kbs)

            self._snap = {
                "ts":               now_ms,
                "cpu_pct":          cpu_pct,
                "cpu_freq_mhz":     cpu_freq_mhz,
                "ram_pct":          ram_pct,
                "ram_used_gb":      ram_used_gb,
                "ram_free_gb":      ram_free_gb,
                "swap_pct":         swap_pct,
                "net_up_kbs":       net_up_kbs,
                "net_down_kbs":     net_down_kbs,
                "net_up_str":       _fmt_speed(net_up_kbs),
                "net_down_str":     _fmt_speed(net_down_kbs),
                "battery_pct":      batt_pct,
                "battery_charging": batt_charging,
                "proc_count":       proc_count,
                "uptime_s":         uptime_s,
                "uptime_str":       uptime_str,
                **self._slow,
            }

            snap = dict(self._snap, **self._static)
            subs = list(self._subscribers)

        logger.debug("[SYSTEM_METRICS_STREAM] cpu=%.1f%% ram=%.1f%% win_fresh=%s",
                     cpu_pct, ram_pct, win_fresh)

        if subs and self._loop:
            msg = {"type": "metrics", "data": snap}
            for q in subs:
                try:
                    self._loop.call_soon_threadsafe(q.put_nowait, msg)
                except Exception:
                    pass


def _fmt_speed(kbs: float) -> str:
    if kbs >= 1024:
        return f"{kbs / 1024:.1f} MB/s"
    return f"{kbs:.0f} KB/s"


# ── Singleton ─────────────────────────────────────────────────────────────────

system_monitor = SystemMonitorService()
