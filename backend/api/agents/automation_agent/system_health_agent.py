from __future__ import annotations

"""
SystemHealthAgent — RAM, GPU, CPU, network, and disk health diagnostics.

All reads go through /proc and PowerShell (via WSL2) so no extra packages
are required.  Results are returned as a structured report dict and also
formatted as human-readable text.

Log tags: [SYS_HEALTH] [SYS_HEALTH_RAM] [SYS_HEALTH_GPU] [SYS_HEALTH_NET]
"""

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class SystemHealthAgent:
    """Full system health diagnostics without extra dependencies."""

    # ── Public API ─────────────────────────────────────────────────────────────

    async def full_report(self) -> dict[str, Any]:
        """Run all diagnostics in parallel and return a combined report dict."""
        ram_task  = asyncio.create_task(self.ram_report())
        cpu_task  = asyncio.create_task(self.cpu_report())
        disk_task = asyncio.create_task(self.disk_report())
        net_task  = asyncio.create_task(self.network_report())
        gpu_task  = asyncio.create_task(self.gpu_report())

        ram, cpu, disk, net, gpu = await asyncio.gather(
            ram_task, cpu_task, disk_task, net_task, gpu_task,
            return_exceptions=True,
        )

        def _safe(r: Any, key: str) -> dict:
            return r if isinstance(r, dict) else {key: "unavailable"}

        report = {
            "ram":     _safe(ram,  "ram"),
            "cpu":     _safe(cpu,  "cpu"),
            "disk":    _safe(disk, "disk"),
            "network": _safe(net,  "network"),
            "gpu":     _safe(gpu,  "gpu"),
        }
        report["text"] = self._format_report(report)
        logger.info("[SYS_HEALTH] full report generated")
        return report

    # ── RAM ────────────────────────────────────────────────────────────────────

    async def ram_report(self) -> dict[str, Any]:
        """Read RAM stats from /proc/meminfo."""
        try:
            def _read() -> dict:
                with open("/proc/meminfo") as f:
                    lines = f.readlines()
                info: dict[str, int] = {}
                for line in lines:
                    m = re.match(r"(\w+):\s+(\d+)\s*kB", line)
                    if m:
                        info[m.group(1)] = int(m.group(2)) * 1024  # bytes
                total     = info.get("MemTotal", 0)
                available = info.get("MemAvailable", 0)
                used      = total - available
                pct       = round(used / total * 100, 1) if total else 0
                return {
                    "total_gb":     round(total / 1e9, 1),
                    "used_gb":      round(used / 1e9, 1),
                    "available_gb": round(available / 1e9, 1),
                    "used_pct":     pct,
                }

            result = await asyncio.to_thread(_read)
            logger.debug("[SYS_HEALTH_RAM] %s", result)
            return result
        except Exception as exc:
            logger.warning("[SYS_HEALTH_RAM] error: %s", exc)
            return {"error": str(exc)}

    # ── CPU ────────────────────────────────────────────────────────────────────

    async def cpu_report(self) -> dict[str, Any]:
        """Read CPU info from /proc/cpuinfo and estimate utilization."""
        try:
            def _read() -> dict:
                with open("/proc/cpuinfo") as f:
                    text = f.read()
                cores  = text.count("processor\t:")
                models = re.findall(r"model name\s*:\s*(.+)", text)
                model  = models[0].strip() if models else "Unknown"
                speeds = re.findall(r"cpu MHz\s*:\s*([\d.]+)", text)
                avg_mhz = (
                    round(sum(float(s) for s in speeds) / len(speeds), 0)
                    if speeds else 0
                )
                return {"model": model, "cores": cores, "avg_mhz": avg_mhz}

            result = await asyncio.to_thread(_read)
            return result
        except Exception as exc:
            return {"error": str(exc)}

    # ── Disk ───────────────────────────────────────────────────────────────────

    async def disk_report(self) -> dict[str, Any]:
        """Use df to get disk usage for /mnt/c (Windows C drive)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "df", "-B1", "/mnt/c",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            raw, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            lines = raw.decode(errors="replace").strip().splitlines()
            if len(lines) < 2:
                return {"error": "df returned no data"}

            parts = lines[1].split()
            total    = int(parts[1])
            used     = int(parts[2])
            avail    = int(parts[3])
            used_pct = round(used / total * 100, 1) if total else 0

            return {
                "drive":     "C:",
                "total_gb":  round(total / 1e9, 1),
                "used_gb":   round(used / 1e9, 1),
                "free_gb":   round(avail / 1e9, 1),
                "used_pct":  used_pct,
            }
        except Exception as exc:
            return {"error": str(exc)}

    # ── GPU ────────────────────────────────────────────────────────────────────

    async def gpu_report(self) -> dict[str, Any]:
        """Query NVIDIA GPU via nvidia-smi, or return basic info via PowerShell."""
        # 1. Try nvidia-smi (most accurate)
        try:
            proc = await asyncio.create_subprocess_exec(
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            raw, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            if proc.returncode == 0:
                line = raw.decode(errors="replace").strip()
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4:
                    return {
                        "name":       parts[0],
                        "vram_total_mb": int(parts[1]) if parts[1].isdigit() else 0,
                        "vram_used_mb":  int(parts[2]) if parts[2].isdigit() else 0,
                        "utilization_pct": int(parts[3]) if parts[3].isdigit() else 0,
                        "temperature_c":   int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else None,
                    }
        except (FileNotFoundError, asyncio.TimeoutError):
            pass

        # 2. Fallback: PowerShell Get-CimInstance Win32_VideoController
        try:
            ps_cmd = (
                "Get-CimInstance Win32_VideoController | "
                "Select-Object -First 1 -ExpandProperty Name"
            )
            proc = await asyncio.create_subprocess_exec(
                "powershell.exe", "-Command", ps_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            raw, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
            name = raw.decode(errors="replace").strip()
            if name:
                return {"name": name, "source": "WMI"}
        except Exception:
            pass

        return {"name": "GPU info unavailable", "source": "none"}

    # ── Network ────────────────────────────────────────────────────────────────

    async def network_report(self) -> dict[str, Any]:
        """Check internet connectivity and basic network stats."""
        import socket
        import time

        result: dict[str, Any] = {}

        # Ping latency to 8.8.8.8
        try:
            proc = await asyncio.create_subprocess_exec(
                "ping", "-c", "3", "-W", "2", "8.8.8.8",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            raw, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
            text = raw.decode(errors="replace")
            m = re.search(r"rtt min/avg/max.*?=([\d.]+)/([\d.]+)/([\d.]+)", text)
            if m:
                result["ping_avg_ms"] = float(m.group(2))
                result["ping_min_ms"] = float(m.group(1))
            result["internet"] = proc.returncode == 0
        except Exception:
            result["internet"] = False

        # DNS resolution check
        try:
            t0 = asyncio.get_event_loop().time()
            await asyncio.to_thread(socket.gethostbyname, "google.com")
            result["dns_ms"] = round((asyncio.get_event_loop().time() - t0) * 1000, 1)
        except Exception:
            result["dns_ms"] = None

        # Active interfaces from /proc/net/dev
        try:
            def _ifaces() -> list[str]:
                with open("/proc/net/dev") as f:
                    lines = f.readlines()[2:]  # skip headers
                active = []
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 2:
                        iface = parts[0].rstrip(":")
                        rx_bytes = int(parts[1])
                        if rx_bytes > 0 and iface not in ("lo",):
                            active.append(iface)
                return active
            result["active_interfaces"] = await asyncio.to_thread(_ifaces)
        except Exception:
            result["active_interfaces"] = []

        logger.debug("[SYS_HEALTH_NET] %s", result)
        return result

    # ── Formatting ─────────────────────────────────────────────────────────────

    def _format_report(self, report: dict[str, Any]) -> str:
        lines = ["System Health Report", "=" * 40]

        ram = report.get("ram", {})
        if "total_gb" in ram:
            bar = _bar(ram.get("used_pct", 0))
            lines.append(
                f"RAM   {bar} {ram['used_pct']}%  "
                f"({ram['used_gb']} GB used / {ram['total_gb']} GB total)"
            )

        cpu = report.get("cpu", {})
        if "model" in cpu:
            lines.append(f"CPU   {cpu['cores']} cores — {cpu['model']}")

        disk = report.get("disk", {})
        if "total_gb" in disk:
            bar = _bar(disk.get("used_pct", 0))
            lines.append(
                f"C:    {bar} {disk['used_pct']}%  "
                f"({disk['used_gb']} GB used / {disk['total_gb']} GB)  "
                f"{disk['free_gb']} GB free"
            )

        gpu = report.get("gpu", {})
        if "name" in gpu:
            name = gpu["name"]
            if "vram_total_mb" in gpu:
                vram_used = gpu.get("vram_used_mb", 0)
                vram_tot  = gpu.get("vram_total_mb", 0)
                util      = gpu.get("utilization_pct", 0)
                temp      = gpu.get("temperature_c")
                temp_str  = f"  {temp}°C" if temp else ""
                lines.append(
                    f"GPU   {name}  "
                    f"VRAM: {vram_used}/{vram_tot} MB  "
                    f"Util: {util}%{temp_str}"
                )
            else:
                lines.append(f"GPU   {name}")

        net = report.get("network", {})
        internet_str = "online" if net.get("internet") else "offline"
        ping_str = f"  ping {net['ping_avg_ms']} ms" if "ping_avg_ms" in net else ""
        dns_str  = f"  DNS {net['dns_ms']} ms" if net.get("dns_ms") else ""
        ifaces   = ", ".join(net.get("active_interfaces", []))
        lines.append(f"NET   {internet_str}{ping_str}{dns_str}  [{ifaces}]")

        # Recommendations
        recs = []
        if ram.get("used_pct", 0) > 85:
            recs.append("• RAM usage is very high — close unused apps to free memory.")
        if disk.get("used_pct", 0) > 80:
            free = disk.get("free_gb", 0)
            recs.append(f"• Disk space is running low ({free} GB remaining) — consider a cleanup.")
        if not net.get("internet"):
            recs.append("• Internet appears offline — check your network connection.")
        if gpu.get("utilization_pct", 0) > 90:
            recs.append("• GPU utilization is very high — heavy workload or process leak.")

        if recs:
            lines.append("")
            lines.append("Recommendations:")
            lines.extend(recs)

        return "\n".join(lines)


def _bar(pct: float, width: int = 10) -> str:
    filled = round(pct / 100 * width)
    return "[" + "█" * filled + "░" * (width - filled) + "]"
