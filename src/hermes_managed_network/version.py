from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata

PACKAGE_NAME = "hermes-managed-network"
API_VERSION = "0.2.0"
WORKER_PROTOCOL_VERSION = "0.1"


@dataclass(frozen=True)
class VersionInfo:
    package_version: str
    api_version: str
    worker_protocol_version: str


def package_version() -> str:
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return "0.0.0+local"


def current_version_info() -> VersionInfo:
    return VersionInfo(
        package_version=package_version(),
        api_version=API_VERSION,
        worker_protocol_version=WORKER_PROTOCOL_VERSION,
    )


def major_minor(version: str) -> str:
    parts = version.split("+", 1)[0].split(".")
    if len(parts) < 2:
        return version
    return ".".join(parts[:2])


def is_worker_compatible(master_protocol: str, worker_protocol: str | None) -> bool:
    if not worker_protocol:
        return True
    return major_minor(master_protocol) == major_minor(worker_protocol)
