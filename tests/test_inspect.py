from hermes_managed_network.inspect import (
    ContainerRecord,
    NodeInventory,
    SystemdServiceRecord,
    parse_docker_ps_json_lines,
    parse_ss_listening_ports,
)


def test_parse_ss_listening_ports_extracts_tcp_ports_and_processes():
    sample = """
State  Recv-Q Send-Q Local Address:Port Peer Address:PortProcess
LISTEN 0      4096       0.0.0.0:80        0.0.0.0:*    users:((\"nginx\",pid=123,fd=6))
LISTEN 0      4096     127.0.0.1:5001      0.0.0.0:*    users:((\"python\",pid=456,fd=7))
LISTEN 0      4096          [::]:443          [::]:*    users:((\"caddy\",pid=789,fd=8))
"""

    ports = parse_ss_listening_ports(sample)

    assert [(p.protocol, p.listen, p.port, p.process) for p in ports] == [
        ("tcp", "0.0.0.0", 80, "nginx"),
        ("tcp", "127.0.0.1", 5001, "python"),
        ("tcp", "::", 443, "caddy"),
    ]


def test_parse_docker_ps_json_lines_extracts_containers():
    sample = '\n'.join(
        [
            '{"Names":"web","Image":"nginx:alpine","Status":"Up 2 hours","Ports":"0.0.0.0:8080->80/tcp"}',
            '{"Name":"worker","Image":"busybox","State":"Exited","Ports":""}',
        ]
    )

    containers, warnings = parse_docker_ps_json_lines(sample)

    assert warnings == []
    assert containers == [
        ContainerRecord(name="web", image="nginx:alpine", status="Up 2 hours", ports=["0.0.0.0:8080->80/tcp"]),
        ContainerRecord(name="worker", image="busybox", status="Exited", ports=[]),
    ]


def test_parse_docker_ps_json_lines_warns_when_docker_unavailable():
    containers, warnings = parse_docker_ps_json_lines("", unavailable=True)

    assert containers == []
    assert warnings == ["docker unavailable or not installed"]


def test_parse_reverse_proxy_config_extracts_caddy_domain_and_upstream_port():
    from hermes_managed_network.inspect import parse_reverse_proxy_config

    domains, mappings = parse_reverse_proxy_config(
        """
example.com {
  reverse_proxy 127.0.0.1:5001
}
"""
    )

    assert domains == ["example.com"]
    assert mappings == {"example.com": 5001}


def test_run_returns_warning_style_status_for_missing_command():
    from hermes_managed_network.inspect import _run

    code, stdout, stderr = _run(["/definitely/missing/hmn-command"])

    assert code == 127
    assert stdout == ""
    assert "command not found" in stderr


def test_node_inventory_round_trips_stable_json_shape():
    inventory = NodeInventory(
        node="local",
        hostname="demo-node",
        os_release="Debian GNU/Linux 12",
        ports=[],
        containers=[ContainerRecord(name="web", image="nginx", status="Up", ports=["8080->80/tcp"])],
        systemd_services=[SystemdServiceRecord(name="caddy.service", active="active", sub="running", description="Caddy")],
        reverse_proxy_domains=["example.com"],
        reverse_proxy_mappings={"example.com": 5001},
        paths=["/srv"],
        warnings=[],
    )

    assert NodeInventory.from_dict(inventory.to_dict()) == inventory
    assert inventory.to_dict()["reverse_proxy_mappings"] == {"example.com": 5001}
