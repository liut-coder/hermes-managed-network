from hermes_managed_network.service_registry import ServiceRecord, ServiceRegistry


def test_registry_upsert_same_service_id_is_idempotent_and_merges_values(tmp_path):
    registry = ServiceRegistry()
    registry.upsert(
        ServiceRecord(
            service_id="docker:web",
            name="web",
            node="node-a",
            kind="docker",
            domains=["example.com"],
            ports=[80],
            runtime="nginx:alpine",
            source="container:web",
        )
    )
    registry.upsert(
        ServiceRecord(
            service_id="docker:web",
            name="web",
            node="node-a",
            kind="docker",
            domains=["example.com", "www.example.com"],
            ports=[80, 443],
            runtime="nginx:alpine",
            source="container:web",
            warnings=["demo warning"],
        )
    )

    services = registry.list_services()
    assert len(services) == 1
    assert services[0].domains == ["example.com", "www.example.com"]
    assert services[0].ports == [80, 443]
    assert services[0].warnings == ["demo warning"]


def test_registry_can_save_and_load_json_file(tmp_path):
    path = tmp_path / "service-registry.json"
    registry = ServiceRegistry(
        [
            ServiceRecord(
                service_id="systemd:caddy.service",
                name="caddy",
                node="node-a",
                kind="systemd",
                domains=["example.com"],
                ports=[443],
                runtime="caddy.service",
                source="systemd:caddy.service",
                docs_path="service/caddy.md",
                monitor={"type": "http", "url": "https://example.com"},
            )
        ]
    )

    registry.save(path)
    loaded = ServiceRegistry.load(path)

    assert loaded.list_services() == registry.list_services()


def test_empty_registry_loads_when_file_missing(tmp_path):
    assert ServiceRegistry.load(tmp_path / "missing.json").list_services() == []


def test_registry_rejects_empty_service_id():
    registry = ServiceRegistry()

    try:
        registry.upsert(
            ServiceRecord(
                service_id="",
                name="broken",
                node="node-a",
                kind="unknown",
                domains=[],
                ports=[],
                runtime=None,
                source="test",
            )
        )
    except ValueError as exc:
        assert "service_id" in str(exc)
    else:
        raise AssertionError("empty service_id should be rejected")
