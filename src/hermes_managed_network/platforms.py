from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class NodeRuntimeProfile(StrEnum):
    """How much code HMN can safely run on a node."""

    FULL_WORKER = "full-worker"
    LITE_WORKER = "lite-worker"
    BEACON_ONLY = "beacon-only"
    PROXY_MANAGED = "proxy-managed"


class ServiceManager(StrEnum):
    """Platform service/periodic runner used to keep node reporting alive."""

    SYSTEMD = "systemd"
    OPENRC = "openrc"
    PROCD = "procd"
    LAUNCHD = "launchd"
    WINDOWS_TASK = "windows-task"
    CRON = "cron"
    LOOP = "loop"
    NONE = "none"


@dataclass(frozen=True)
class CapabilityProbe:
    """Normalized feature flags collected from a node bootstrap probe.

    Keep this model dependency-free and conservative so old routers and odd
    vendor firmware can be represented without pretending they are standard
    Linux servers.
    """

    os_family: str = "unknown"
    has_sh: bool = False
    has_bash: bool = False
    has_curl: bool = False
    has_wget: bool = False
    has_python3: bool = False
    has_busybox: bool = False
    has_systemctl: bool = False
    has_openrc: bool = False
    has_procd: bool = False
    has_launchctl: bool = False
    has_powershell: bool = False
    has_crond: bool = False
    writable_etc: bool = False
    writable_tmp: bool = False
    memory_mb: int | None = None
    disk_free_mb: int | None = None

    @property
    def has_http_client(self) -> bool:
        return self.has_curl or self.has_wget


@dataclass(frozen=True)
class RuntimeCapabilities:
    runtime: NodeRuntimeProfile
    service_manager: ServiceManager
    can_report_heartbeat: bool
    can_poll_tasks: bool
    can_execute_tasks: bool
    requirements: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def probe_from_facts(facts: dict[str, object]) -> CapabilityProbe:
    """Build a normalized capability probe from heartbeat facts.

    Heartbeats may send either a nested ``capabilities`` object or flat probe
    keys. Unknown/missing values stay conservative.
    """

    raw = facts.get("capabilities") if isinstance(facts.get("capabilities"), dict) else facts
    assert isinstance(raw, dict)
    return CapabilityProbe(
        os_family=str(raw.get("os_family") or "unknown"),
        has_sh=_truthy(raw.get("has_sh")),
        has_bash=_truthy(raw.get("has_bash")),
        has_curl=_truthy(raw.get("has_curl")),
        has_wget=_truthy(raw.get("has_wget")),
        has_python3=_truthy(raw.get("has_python3")),
        has_busybox=_truthy(raw.get("has_busybox")),
        has_systemctl=_truthy(raw.get("has_systemctl")),
        has_openrc=_truthy(raw.get("has_openrc")),
        has_procd=_truthy(raw.get("has_procd")),
        has_launchctl=_truthy(raw.get("has_launchctl")),
        has_powershell=_truthy(raw.get("has_powershell")),
        has_crond=_truthy(raw.get("has_crond")),
        writable_etc=_truthy(raw.get("writable_etc")),
        writable_tmp=_truthy(raw.get("writable_tmp")),
        memory_mb=_optional_int(raw.get("memory_mb")),
        disk_free_mb=_optional_int(raw.get("disk_free_mb")),
    )


def detect_service_manager(probe: CapabilityProbe) -> ServiceManager:
    """Pick the best service manager without assuming systemd everywhere."""

    os_family = probe.os_family.lower()
    if probe.has_systemctl:
        return ServiceManager.SYSTEMD
    if probe.has_procd or os_family == "openwrt":
        return ServiceManager.PROCD
    if probe.has_openrc:
        return ServiceManager.OPENRC
    if probe.has_launchctl or os_family == "darwin":
        return ServiceManager.LAUNCHD
    if probe.has_powershell or os_family == "windows":
        return ServiceManager.WINDOWS_TASK
    if probe.has_crond:
        return ServiceManager.CRON
    if probe.has_sh and probe.has_http_client:
        return ServiceManager.LOOP
    return ServiceManager.NONE


def classify_capabilities(probe: CapabilityProbe) -> RuntimeCapabilities:
    """Map raw probe facts to an HMN runtime profile.

    This is intentionally conservative: full task execution is only enabled for
    Python-capable, writable nodes with a known service manager. Small routers
    default to heartbeat/limited worker modes, and non-agentable devices become
    proxy-managed assets.
    """

    service_manager = detect_service_manager(probe)
    http = probe.has_http_client

    if probe.has_sh and http and probe.has_python3 and probe.writable_etc and service_manager in {
        ServiceManager.SYSTEMD,
        ServiceManager.OPENRC,
        ServiceManager.PROCD,
        ServiceManager.LAUNCHD,
        ServiceManager.WINDOWS_TASK,
        ServiceManager.CRON,
    }:
        return RuntimeCapabilities(
            runtime=NodeRuntimeProfile.FULL_WORKER,
            service_manager=service_manager,
            can_report_heartbeat=True,
            can_poll_tasks=True,
            can_execute_tasks=True,
            requirements=["python3", "http-client", str(service_manager)],
            notes=["task execution still requires explicit operator enablement"],
        )

    if probe.has_powershell and http and service_manager == ServiceManager.WINDOWS_TASK:
        return RuntimeCapabilities(
            runtime=NodeRuntimeProfile.FULL_WORKER,
            service_manager=service_manager,
            can_report_heartbeat=True,
            can_poll_tasks=True,
            can_execute_tasks=True,
            requirements=["powershell", "http-client", "Task Scheduler"],
            notes=["Windows Task Scheduler worker supports signed task polling", "task execution still requires explicit operator enablement"],
        )

    if probe.has_powershell and service_manager == ServiceManager.WINDOWS_TASK:
        return RuntimeCapabilities(
            runtime=NodeRuntimeProfile.BEACON_ONLY,
            service_manager=service_manager,
            can_report_heartbeat=True,
            can_poll_tasks=False,
            can_execute_tasks=False,
            requirements=["powershell", "Task Scheduler"],
            notes=["Windows 当前以计划任务心跳模式运行", "默认不执行远程下发命令"],
        )

    if probe.has_sh and http and (probe.has_busybox or probe.has_crond or service_manager in {ServiceManager.PROCD, ServiceManager.OPENRC}):
        return RuntimeCapabilities(
            runtime=NodeRuntimeProfile.LITE_WORKER,
            service_manager=service_manager if service_manager != ServiceManager.LOOP else ServiceManager.CRON,
            can_report_heartbeat=True,
            can_poll_tasks=True,
            can_execute_tasks=False,
            requirements=["posix-sh", "http-client"],
            notes=["no arbitrary shell execution by default", "fit for NAS/OpenWrt/BusyBox/old routers"],
        )

    if probe.has_sh and http:
        return RuntimeCapabilities(
            runtime=NodeRuntimeProfile.BEACON_ONLY,
            service_manager=ServiceManager.NONE,
            can_report_heartbeat=True,
            can_poll_tasks=False,
            can_execute_tasks=False,
            requirements=["posix-sh", "http-client"],
            notes=["heartbeat only; use external proxy node for management"],
        )

    return RuntimeCapabilities(
        runtime=NodeRuntimeProfile.PROXY_MANAGED,
        service_manager=ServiceManager.NONE,
        can_report_heartbeat=False,
        can_poll_tasks=False,
        can_execute_tasks=False,
        requirements=["nearby managed proxy node"],
        notes=["device cannot host HMN agent safely"],
    )



def _windows_worker_task_command(worker_path: str) -> str:
    escaped = worker_path.replace('"', '""')
    return f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{escaped}"'



def render_windows_task_installer(worker_path: str = r"C:\ProgramData\HermesManagedNetwork\hmn-worker.ps1") -> str:
    task_name = "HermesManagedNetworkHeartbeat"
    task_command = _windows_worker_task_command(worker_path)
    return f"""$taskName = \"{task_name}\"
$workerPath = \"{worker_path}\"
$taskCommand = '{task_command}'
schtasks /Create /SC MINUTE /MO 1 /TN $taskName /TR $taskCommand /RU SYSTEM /RL HIGHEST /F | Out-Null
schtasks /Run /TN $taskName | Out-Null
"""



def render_windows_task_uninstaller(base_dir: str = r"C:\ProgramData\HermesManagedNetwork") -> str:
    task_name = "HermesManagedNetworkHeartbeat"
    return f"""$taskName = \"{task_name}\"
$baseDir = \"{base_dir}\"
schtasks /Delete /TN $taskName /F 2>$null | Out-Null
if (Test-Path -LiteralPath $baseDir) {{
  Remove-Item -LiteralPath $baseDir -Recurse -Force
}}
"""



def render_service_manager_installer(service_manager: ServiceManager | str, worker_path: str = "/usr/local/bin/hmn-worker") -> str:
    """Render the service-manager specific wiring for an installed worker.

    This adapter stays deliberately small: common download/env setup belongs to
    the caller, while this function only owns how the node keeps the worker
    running periodically.
    """

    manager = ServiceManager(service_manager)
    if manager == ServiceManager.SYSTEMD:
        return f"""cat >/etc/systemd/system/hermes-managed-network-heartbeat.service <<'EOF'
[Unit]
Description=Hermes Managed Network heartbeat and worker
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/hermes-managed-network/node.env
Environment=HMN_ENABLE_EXEC=0
ExecStart={worker_path}
EOF
cat >/etc/systemd/system/hermes-managed-network-heartbeat.timer <<'EOF'
[Unit]
Description=Run Hermes Managed Network heartbeat and worker every minute

[Timer]
OnBootSec=30s
OnUnitActiveSec=60s
Unit=hermes-managed-network-heartbeat.service

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now hermes-managed-network-heartbeat.timer
"""
    if manager == ServiceManager.WINDOWS_TASK:
        return render_windows_task_installer(worker_path)
    if manager == ServiceManager.CRON:
        return f"""tmp_cron=$(mktemp)
crontab -l 2>/dev/null | grep -v 'hermes-managed-network heartbeat' >"$tmp_cron" || true
printf '%s\n' '* * * * * {worker_path} # hermes-managed-network heartbeat' >>"$tmp_cron"
crontab "$tmp_cron"
rm -f "$tmp_cron"
"""
    if manager == ServiceManager.PROCD:
        return f"""cat >/etc/init.d/hermes-managed-network <<'EOF'
#!/bin/sh /etc/rc.common
START=95
USE_PROCD=1
start_service() {{
  procd_open_instance
  procd_set_param command /bin/sh -c 'while true; do {worker_path}; sleep 60; done'
  procd_set_param respawn
  procd_close_instance
}}
EOF
chmod 0755 /etc/init.d/hermes-managed-network
/etc/init.d/hermes-managed-network enable
/etc/init.d/hermes-managed-network restart
"""
    if manager == ServiceManager.OPENRC:
        return f"""cat >/etc/init.d/hermes-managed-network <<'EOF'
#!/sbin/openrc-run
command="/bin/sh"
command_args="-c 'while true; do {worker_path}; sleep 60; done'"
command_background=true
pidfile="/run/hermes-managed-network.pid"
EOF
chmod 0755 /etc/init.d/hermes-managed-network
rc-update add hermes-managed-network default
rc-service hermes-managed-network restart
"""
    if manager == ServiceManager.LOOP:
        return f"""cat >/usr/local/bin/hmn-worker-loop <<'EOF'
#!/bin/sh
while true; do
  {worker_path}
  sleep 60
done
EOF
chmod 0755 /usr/local/bin/hmn-worker-loop
nohup /usr/local/bin/hmn-worker-loop >/var/log/hmn-worker.log 2>&1 &
"""
    return """echo '不支持在该节点安装常驻 worker：service manager 为 none/unsupported。请改用 proxy-managed 或手动外部调度。' >&2
exit 1
"""


def render_capability_probe() -> str:
    """Return a minimal POSIX-sh probe suitable for old routers.

    The output is line-oriented KEY=0/1 text on purpose. It avoids JSON, jq,
    arrays, bashisms, and GNU-only assumptions so a future join script can parse
    it on BusyBox-class systems.
    """

    return """#!/bin/sh
set -u
has() { command -v "$1" >/dev/null 2>&1 && echo 1 || echo 0; }
writable() { [ -d "$1" ] && [ -w "$1" ] && echo 1 || echo 0; }
printf 'os_family='
case "$(uname -s 2>/dev/null || echo unknown)" in
  Linux) printf 'linux\n' ;;
  Darwin) printf 'darwin\n' ;;
  CYGWIN*|MINGW*|MSYS*) printf 'windows\n' ;;
  *) printf 'unknown\n' ;;
esac
printf 'has_sh=%s\n' "$(has sh)"
printf 'has_bash=%s\n' "$(has bash)"
printf 'has_curl=%s\n' "$(has curl)"
printf 'has_wget=%s\n' "$(has wget)"
printf 'has_python3=%s\n' "$(has python3)"
printf 'has_busybox=%s\n' "$(has busybox)"
printf 'has_systemctl=%s\n' "$(has systemctl)"
printf 'has_openrc=%s\n' "$(has openrc)"
printf 'has_procd=%s\n' "$(has procd)"
printf 'has_launchctl=%s\n' "$(has launchctl)"
printf 'has_powershell=%s\n' "$(has powershell)"
printf 'has_crond=%s\n' "$(has crond)"
printf 'writable_etc=%s\n' "$(writable /etc)"
printf 'writable_tmp=%s\n' "$(writable /tmp)"
"""
