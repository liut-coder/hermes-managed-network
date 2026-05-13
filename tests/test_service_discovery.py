from hermes_managed_network.service_discovery import apply_discovered_services, discover_services_from_text
from hermes_managed_network.storage import SQLiteStore, ServiceRecord


SYSTEMD_OUTPUT = """
nginx.service loaded active running A high performance web server and a reverse proxy server
postgresql.service loaded active running PostgreSQL RDBMS
"""

SYSTEMD_OUTPUT_WITH_BULLET = """
● nginx.service loaded active running A high performance web server and a reverse proxy server
"""

DOCKER_OUTPUT = """
{"Names":"web-app","Image":"nginx:alpine","Ports":"0.0.0.0:8080->80/tcp, :::8443->443/tcp"}
{"Names":"postgresql","Image":"postgres:16","Ports":"5432/tcp"}
"""

COMPOSE_OUTPUT = """
services:
  web-app:
    image: nginx:alpine
    env_file:
      - /srv/web-app/.env
    volumes:
      - /srv/web-app/data:/var/lib/app
      - /srv/web-app/config/nginx.conf:/etc/nginx/nginx.conf:ro
    healthcheck:
      test: ["CMD", "curl", "-f", "http://127.0.0.1:8080/readyz"]
"""

CADDY_OUTPUT = """
app.example.com {
    reverse_proxy 127.0.0.1:8080
}

status.example.com {
    reverse_proxy 127.0.0.1:3001
}
"""

NGINX_OUTPUT = """
server {
    server_name api.example.com;
    location /healthz {
        proxy_pass http://127.0.0.1:9000/healthz;
    }
}
"""

PORTS_OUTPUT = """
State  Recv-Q Send-Q Local Address:Port  Peer Address:PortProcess
LISTEN 0      511          0.0.0.0:80        0.0.0.0:*    users:(("nginx",pid=123,fd=6))
LISTEN 0      4096       127.0.0.1:5432      0.0.0.0:*    users:(("postgres",pid=456,fd=7))
LISTEN 0      128                *:9000            *:*    users:(("custom-api",pid=789,fd=8))
"""


def test_discover_services_from_text_parses_systemd_docker_and_ports_with_stable_ids():
    records = discover_services_from_text(
        "node-a",
        systemd_output=SYSTEMD_OUTPUT,
        docker_output=DOCKER_OUTPUT,
        ports_output=PORTS_OUTPUT,
    )

    by_id = {record.service_id: record for record in records}

    assert "svc_node-a_nginx" in by_id
    nginx = by_id["svc_node-a_nginx"]
    assert nginx.name == "nginx"
    assert nginx.node_id == "node-a"
    assert nginx.kind == "systemd"
    assert nginx.runtime == "nginx.service"
    assert nginx.ports == [80]
    assert nginx.source == "discovery"
    assert nginx.metadata["systemd"]["description"] == "A high performance web server and a reverse proxy server"
    assert nginx.metadata["listen_ports"] == [{"address": "0.0.0.0", "port": 80, "process": "nginx"}]

    assert "svc_node-a_web-app" in by_id
    docker = by_id["svc_node-a_web-app"]
    assert docker.kind == "docker"
    assert docker.runtime == "nginx:alpine"
    assert docker.ports == [8080, 8443]
    assert docker.metadata["docker"]["image"] == "nginx:alpine"
    assert docker.metadata["docker"]["container_name"] == "web-app"

    assert "svc_node-a_custom-api" in by_id
    port = by_id["svc_node-a_custom-api"]
    assert port.kind == "port"
    assert port.runtime == "ss"
    assert port.ports == [9000]
    assert port.metadata["listen_ports"] == [{"address": "*", "port": 9000, "process": "custom-api"}]


def test_discover_services_from_text_deduplicates_same_systemd_docker_port_names():
    records = discover_services_from_text(
        "node-a",
        systemd_output=SYSTEMD_OUTPUT,
        docker_output=DOCKER_OUTPUT,
        ports_output=PORTS_OUTPUT,
    )

    names = [record.name for record in records]
    assert names.count("postgresql") == 1
    postgresql = next(record for record in records if record.name == "postgresql")
    assert postgresql.kind == "systemd"
    assert postgresql.ports == [5432]
    assert postgresql.metadata["docker"]["image"] == "postgres:16"
    assert postgresql.metadata["listen_ports"][0]["process"] == "postgres"


def test_discover_services_from_text_parses_systemd_bullet_prefix():
    records = discover_services_from_text("node-a", systemd_output=SYSTEMD_OUTPUT_WITH_BULLET)

    assert len(records) == 1
    assert records[0].service_id == "svc_node-a_nginx"
    assert records[0].runtime == "nginx.service"
    assert records[0].metadata["systemd"]["description"] == "A high performance web server and a reverse proxy server"


def test_discover_services_from_text_does_not_treat_bare_docker_exposed_port_as_host_port():
    records = discover_services_from_text(
        "node-a",
        docker_output='{"Names":"postgresql","Image":"postgres:16","Ports":"5432/tcp"}\n',
    )

    assert len(records) == 1
    assert records[0].service_id == "svc_node-a_postgresql"
    assert records[0].ports == []
    assert records[0].metadata["docker"]["ports"] == "5432/tcp"


def test_apply_discovered_services_writes_records_and_audit(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    records = discover_services_from_text("node-a", systemd_output=SYSTEMD_OUTPUT)

    saved = apply_discovered_services(store, "node-a", records, source="unit-test")

    assert len(saved) == 2
    stored = store.list_service_records(node_id="node-a")
    assert [record.service_id for record in stored] == ["svc_node-a_nginx", "svc_node-a_postgresql"]
    assert all(record.source == "unit-test" for record in stored)
    events = store.list_audit_events()
    assert len(events) == 1
    assert events[0].action == "service_discovery"
    assert events[0].subject_type == "node"
    assert events[0].subject_id == "node-a"
    assert events[0].details["source"] == "unit-test"
    assert events[0].details["service_count"] == 2
    assert events[0].details["service_ids"] == ["svc_node-a_nginx", "svc_node-a_postgresql"]
    assert events[0].details["changes"][0]["change"] == "create"
    assert events[0].details["changes"][0]["diff"]["service_id"]["after"] == "svc_node-a_nginx"


def test_apply_discovered_services_preserves_curated_fields_and_merges_discovery(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_service_record(
        ServiceRecord(
            service_id="svc_node-a_nginx",
            name="curated nginx",
            node_id="node-a",
            kind="manual",
            runtime="curated-runtime",
            domains=["app.example.com"],
            ports=[443],
            deploy_path="/srv/nginx",
            config_paths=["/etc/nginx/nginx.conf"],
            env_paths=["/srv/nginx/.env"],
            data_paths=["/var/lib/nginx"],
            health_check_url="https://app.example.com/healthz",
            monitor_enabled=True,
            docs_path="docs/services/nginx.md",
            source="manual",
            status="maintenance",
            metadata={
                "owner": "platform",
                "docker": {"image": "curated-image"},
                "listen_ports": [{"address": "0.0.0.0", "port": 443, "process": "nginx"}],
            },
        )
    )
    discovered = discover_services_from_text(
        "node-a",
        systemd_output="nginx.service loaded active running A high performance web server\n",
        ports_output='LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:(("nginx",pid=123,fd=6))\n',
    )

    saved = apply_discovered_services(store, "node-a", discovered, source="unit-test")

    assert len(saved) == 1
    stored = store.load_service_record("svc_node-a_nginx")
    assert stored is not None
    assert stored.name == "curated nginx"
    assert stored.kind == "manual"
    assert stored.runtime == "curated-runtime"
    assert stored.source == "manual"
    assert stored.domains == ["app.example.com"]
    assert stored.deploy_path == "/srv/nginx"
    assert stored.config_paths == ["/etc/nginx/nginx.conf"]
    assert stored.env_paths == ["/srv/nginx/.env"]
    assert stored.data_paths == ["/var/lib/nginx"]
    assert stored.health_check_url == "https://app.example.com/healthz"
    assert stored.monitor_enabled is True
    assert stored.docs_path == "docs/services/nginx.md"
    assert stored.status == "maintenance"
    assert stored.ports == [80, 443]
    assert stored.metadata["owner"] == "platform"
    assert stored.metadata["docker"] == {"image": "curated-image"}
    assert stored.metadata["systemd"]["unit"] == "nginx.service"
    assert stored.metadata["listen_ports"] == [
        {"address": "0.0.0.0", "port": 443, "process": "nginx"},
        {"address": "0.0.0.0", "port": 80, "process": "nginx"},
    ]
    assert stored.metadata["discovery_source"] == "unit-test"


def test_discover_services_from_text_enriches_service_registry_with_compose_proxy_and_health_checks():
    records = discover_services_from_text(
        "node-a",
        docker_output='{"Names":"web-app","Image":"nginx:alpine","Ports":"0.0.0.0:8080->80/tcp"}\n'
        '{"Names":"hmn-status","Image":"louislam/uptime-kuma","Ports":"127.0.0.1:3001->3001/tcp"}\n'
        '{"Names":"custom-api","Image":"ghcr.io/example/api","Ports":"127.0.0.1:9000->9000/tcp"}\n',
        compose_output=COMPOSE_OUTPUT,
        caddy_output=CADDY_OUTPUT,
        nginx_output=NGINX_OUTPUT,
    )

    by_id = {record.service_id: record for record in records}

    web = by_id["svc_node-a_web-app"]
    assert web.deploy_path == "/srv/web-app"
    assert web.env_paths == ["/srv/web-app/.env"]
    assert web.data_paths == ["/srv/web-app/data"]
    assert web.config_paths == ["/srv/web-app/config/nginx.conf"]
    assert web.domains == ["app.example.com"]
    assert web.health_check_url == "http://127.0.0.1:8080/readyz"
    assert web.metadata["reverse_proxy"]["source"] == "caddy"
    assert web.metadata["health_check"]["source"] == "compose"

    api = by_id["svc_node-a_custom-api"]
    assert api.domains == ["api.example.com"]
    assert api.health_check_url == "http://127.0.0.1:9000/healthz"
    assert api.metadata["reverse_proxy"]["source"] == "nginx"

    status = by_id["svc_node-a_hmn-status"]
    assert status.domains == ["status.example.com"]
    assert status.metadata["exposure"]["scope"] == "status-page"
