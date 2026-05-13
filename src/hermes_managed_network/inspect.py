from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class PortRecord:
    protocol: str
    listen: str
    port: int
    process: str | None = None


@dataclass
class ContainerRecord:
    name: str
    image: str
    status: str
    ports: list[str]


@dataclass
class SystemdServiceRecord:
    name: str
    active: str
    sub: str
    description: str


@dataclass
class NodeInventory:
    node: str
    hostname: str
    os_release: str
    ports: list[PortRecord]
    containers: list[ContainerRecord]
    systemd_services: list[SystemdServiceRecord]
    reverse_proxy_domains: list[str]
    paths: list[str]
    warnings: list[str]
    reverse_proxy_mappings: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "NodeInventory":
        return cls(
            node=str(data.get("node", "")),
            hostname=str(data.get("hostname", "")),
            os_release=str(data.get("os_release", "")),
            ports=[PortRecord(**item) for item in data.get("ports", [])],
            containers=[ContainerRecord(**item) for item in data.get("containers", [])],
            systemd_services=[SystemdServiceRecord(**item) for item in data.get("systemd_services", [])],
            reverse_proxy_domains=list(data.get("reverse_proxy_domains", [])),
            paths=list(data.get("paths", [])),
            warnings=list(data.get("warnings", [])),
            reverse_proxy_mappings={k: int(v) for k, v in dict(data.get("reverse_proxy_mappings", {})).items()},
        )


def parse_ss_listening_ports(text: str) -> list[PortRecord]:
    records: list[PortRecord] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("State") or "LISTEN" not in line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        local = parts[3]
        match = re.match(r"^(?P<host>\[.*\]|.*):(?P<port>\d+)$", local)
        if not match:
            continue
        host = match.group("host").strip("[]") or "0.0.0.0"
        process_match = re.search(r'users:\(\("([^"]+)"', line)
        records.append(
            PortRecord(
                protocol="tcp",
                listen=host,
                port=int(match.group("port")),
                process=process_match.group(1) if process_match else None,
            )
        )
    return records


def parse_docker_ps_json_lines(text: str, *, unavailable: bool = False) -> tuple[list[ContainerRecord], list[str]]:
    if unavailable:
        return [], ["docker unavailable or not installed"]
    containers: list[ContainerRecord] = []
    warnings: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            warnings.append("failed to parse docker ps json line")
            continue
        ports = [part.strip() for part in str(item.get("Ports", "")).split(",") if part.strip()]
        containers.append(
            ContainerRecord(
                name=str(item.get("Names") or item.get("Name") or ""),
                image=str(item.get("Image") or ""),
                status=str(item.get("Status") or item.get("State") or ""),
                ports=ports,
            )
        )
    return containers, warnings


def parse_systemd_services(text: str) -> list[SystemdServiceRecord]:
    services: list[SystemdServiceRecord] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("UNIT ") or line.startswith("LOAD "):
            continue
        if line.startswith("●"):
            line = line.removeprefix("●").strip()
        parts = line.split(None, 4)
        if len(parts) < 5 or not parts[0].endswith(".service"):
            continue
        services.append(SystemdServiceRecord(name=parts[0], active=parts[2], sub=parts[3], description=parts[4]))
    return services


def parse_reverse_proxy_config(text: str) -> tuple[list[str], dict[str, int]]:
    domains: list[str] = []
    mappings: dict[str, int] = {}
    current_domain: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        site_match = re.match(r"^([A-Za-z0-9.-]+)\s*\{", line)
        if site_match and "." in site_match.group(1):
            current_domain = site_match.group(1)
            domains.append(current_domain)
            continue
        server_name = re.search(r"server_name\s+([^;]+);", line)
        if server_name:
            for domain in server_name.group(1).split():
                if "." in domain and domain not in domains:
                    domains.append(domain)
                    current_domain = domain
        upstream = re.search(r"(?:reverse_proxy|proxy_pass)\s+(?:https?://)?(?:127\.0\.0\.1|localhost):(?P<port>\d+)", line)
        if upstream and current_domain:
            mappings[current_domain] = int(upstream.group("port"))
    return sorted(set(domains)), mappings


def _run(command: list[str]) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(command, check=False, text=True, capture_output=True, timeout=5)
    except subprocess.TimeoutExpired:
        return 124, "", f"command timed out: {' '.join(command)}"
    except FileNotFoundError:
        return 127, "", f"command not found: {command[0]}"
    return completed.returncode, completed.stdout, completed.stderr


def collect_local_inventory(
    *,
    node: str = "local",
    runner: Callable[[list[str]], tuple[int, str, str]] = _run,
    proxy_config_paths: list[Path] | None = None,
) -> NodeInventory:
    warnings: list[str] = []
    code, hostname_out, _ = runner(["hostname"])
    hostname = hostname_out.strip() if code == 0 and hostname_out.strip() else node

    os_release = ""
    try:
        os_text = Path("/etc/os-release").read_text()
        os_release = _extract_os_pretty_name(os_text)
    except OSError:
        warnings.append("/etc/os-release unavailable")

    code, ss_out, ss_err = runner(["ss", "-ltnp"])
    ports = parse_ss_listening_ports(ss_out) if code == 0 else []
    if code != 0:
        warnings.append(f"ss unavailable: {ss_err.strip() or code}")

    if shutil.which("docker"):
        code, docker_out, docker_err = runner(["docker", "ps", "-a", "--format", "json"])
        containers, docker_warnings = parse_docker_ps_json_lines(docker_out, unavailable=code != 0)
        if code != 0 and docker_err.strip():
            docker_warnings.append(docker_err.strip())
        warnings.extend(docker_warnings)
    else:
        containers, docker_warnings = parse_docker_ps_json_lines("", unavailable=True)
        warnings.extend(docker_warnings)

    if shutil.which("systemctl"):
        code, systemd_out, systemd_err = runner(["systemctl", "list-units", "--type=service", "--state=running", "--no-pager"])
        systemd_services = parse_systemd_services(systemd_out) if code == 0 else []
        if code != 0:
            warnings.append(f"systemctl unavailable: {systemd_err.strip() or code}")
    else:
        systemd_services = []
        warnings.append("systemctl unavailable or not installed")

    domains, mappings = collect_reverse_proxy_domains(proxy_config_paths=proxy_config_paths)
    existing_paths = [path for path in ["/srv", "/opt", "/home", "/www"] if Path(path).exists()]
    return NodeInventory(
        node=node,
        hostname=hostname,
        os_release=os_release,
        ports=ports,
        containers=containers,
        systemd_services=systemd_services,
        reverse_proxy_domains=domains,
        reverse_proxy_mappings=mappings,
        paths=existing_paths,
        warnings=warnings,
    )


def collect_reverse_proxy_domains(*, proxy_config_paths: list[Path] | None = None) -> tuple[list[str], dict[str, int]]:
    paths = proxy_config_paths or [Path("/etc/caddy/Caddyfile"), Path("/etc/nginx/sites-enabled")]
    domains: list[str] = []
    mappings: dict[str, int] = {}
    for path in paths:
        candidates = [path]
        if path.is_dir():
            candidates = [child for child in path.iterdir() if child.is_file()]
        for candidate in candidates:
            try:
                found, found_mappings = parse_reverse_proxy_config(candidate.read_text())
            except OSError:
                continue
            domains.extend(found)
            mappings.update(found_mappings)
    return sorted(set(domains)), mappings


def _extract_os_pretty_name(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def inventory_to_json(inventory: NodeInventory) -> str:
    return json.dumps(inventory.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def inventory_from_json(text: str) -> NodeInventory:
    return NodeInventory.from_dict(json.loads(text))
