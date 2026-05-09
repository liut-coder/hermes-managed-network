# Service manager templates

This directory is reserved for platform-specific service managers.

Current implementation:

- `scripts/install-master.sh`: Linux systemd installer

Planned templates:

- `systemd/`: Debian, Ubuntu, Fedora, CentOS, generic systemd Linux
- `openrc/`: Alpine Linux
- `procd/`: OpenWrt
- `launchd/`: macOS
- `windows/`: Windows service / PowerShell

Design rule: keep the core HMN CLI/API behavior identical across platforms, and only vary service-manager wiring here.
