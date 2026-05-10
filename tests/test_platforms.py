from __future__ import annotations

from hermes_managed_network.platforms import (
    CapabilityProbe,
    NodeRuntimeProfile,
    ServiceManager,
    classify_capabilities,
    detect_service_manager,
    probe_from_facts,
    render_capability_probe,
    render_service_manager_installer,
)


def test_capability_probe_script_is_posix_and_avoids_bashisms():
    script = render_capability_probe()

    assert script.startswith("#!/bin/sh")
    assert "command -v" in script
    assert "systemctl" in script
    assert "procd" in script
    assert "[[" not in script
    assert "function " not in script


def test_systemd_linux_gets_full_worker_profile():
    probe = CapabilityProbe(
        os_family="linux",
        has_sh=True,
        has_curl=True,
        has_python3=True,
        has_systemctl=True,
        has_crond=True,
        writable_etc=True,
    )

    assert detect_service_manager(probe) == ServiceManager.SYSTEMD
    profile = classify_capabilities(probe)
    assert profile.runtime == NodeRuntimeProfile.FULL_WORKER
    assert profile.service_manager == ServiceManager.SYSTEMD
    assert profile.can_execute_tasks is True


def test_busybox_router_gets_lite_worker_profile():
    probe = CapabilityProbe(
        os_family="linux",
        has_sh=True,
        has_wget=True,
        has_busybox=True,
        has_crond=True,
        writable_tmp=True,
    )

    profile = classify_capabilities(probe)
    assert profile.runtime == NodeRuntimeProfile.LITE_WORKER
    assert profile.service_manager == ServiceManager.CRON
    assert profile.can_execute_tasks is False
    assert "posix-sh" in profile.requirements


def test_wget_only_device_gets_beacon_profile():
    probe = CapabilityProbe(os_family="unknown", has_sh=True, has_wget=True, writable_tmp=True)

    profile = classify_capabilities(probe)
    assert profile.runtime == NodeRuntimeProfile.BEACON_ONLY
    assert profile.service_manager == ServiceManager.NONE
    assert profile.can_report_heartbeat is True
    assert profile.can_execute_tasks is False


def test_unmanageable_device_gets_proxy_managed_profile():
    probe = CapabilityProbe(os_family="unknown")

    profile = classify_capabilities(probe)
    assert profile.runtime == NodeRuntimeProfile.PROXY_MANAGED
    assert profile.can_report_heartbeat is False
    assert profile.can_execute_tasks is False


def test_platform_specific_service_managers_are_detected():
    assert detect_service_manager(CapabilityProbe(os_family="linux", has_openrc=True)) == ServiceManager.OPENRC
    assert detect_service_manager(CapabilityProbe(os_family="openwrt", has_procd=True)) == ServiceManager.PROCD
    assert detect_service_manager(CapabilityProbe(os_family="darwin", has_launchctl=True)) == ServiceManager.LAUNCHD
    assert detect_service_manager(CapabilityProbe(os_family="windows", has_powershell=True)) == ServiceManager.WINDOWS_TASK


def test_probe_from_heartbeat_facts_classifies_runtime_profile():
    facts = {
        "capabilities": {
            "os_family": "linux",
            "has_sh": True,
            "has_curl": True,
            "has_python3": True,
            "has_systemctl": True,
            "writable_etc": True,
        }
    }

    probe = probe_from_facts(facts)
    profile = classify_capabilities(probe)

    assert probe.has_http_client is True
    assert profile.runtime == NodeRuntimeProfile.FULL_WORKER
    assert profile.service_manager == ServiceManager.SYSTEMD


def test_service_manager_installer_renders_cron_adapter_without_systemctl():
    script = render_service_manager_installer(ServiceManager.CRON, worker_path="/usr/local/bin/hmn-worker")

    assert "crontab" in script
    assert "* * * * * /usr/local/bin/hmn-worker" in script
    assert "systemctl" not in script


def test_service_manager_installer_refuses_none_adapter():
    script = render_service_manager_installer(ServiceManager.NONE, worker_path="/usr/local/bin/hmn-worker")

    assert "不支持在该节点安装常驻 worker" in script
    assert "exit 1" in script
