from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.inventory import Node
from hermes_managed_network.storage import SQLiteStore


def _managed_node() -> Node:
    return Node(
        node_id="node_docs_1",
        fingerprint="sha256:docs",
        hostname="docs-host",
        addresses=["10.0.0.5"],
        trust_level="B",
        labels=["web", "prod"],
        status="managed",
        permission_bundles=["observe", "task"],
        ssh_host="docs.example.internal",
        ssh_user="root",
        ssh_port=2222,
        network_provider="headscale",
        network_node_id="42",
        network_ip="100.64.0.5",
        network_tags=["tag:web"],
        network_online=True,
    )


def test_docs_server_generates_machine_document(tmp_path):
    db = tmp_path / "hmn.db"
    output_root = tmp_path / "docs"
    SQLiteStore(db).save_node(_managed_node())

    result = CliRunner().invoke(
        app,
        ["docs", "server", "node_docs_1", "--db", str(db), "--output-root", str(output_root)],
    )

    assert result.exit_code == 0
    doc_path = output_root / "server" / "docs-host" / "README.md"
    assert doc_path.exists()
    content = doc_path.read_text(encoding="utf-8")
    assert "# docs-host" in content
    assert "节点 ID: `node_docs_1`" in content
    assert "SSH: `root@docs.example.internal -p 2222`" in content
    assert "Headscale: `42` / `100.64.0.5` / online" in content
    assert "标签: `web`, `prod`" in content
    assert str(doc_path) in result.stdout


def test_docs_index_generates_machine_index(tmp_path):
    db = tmp_path / "hmn.db"
    output_root = tmp_path / "docs"
    SQLiteStore(db).save_node(_managed_node())

    result = CliRunner().invoke(app, ["docs", "index", "--db", str(db), "--output-root", str(output_root)])

    assert result.exit_code == 0
    index_path = output_root / "server" / "README.md"
    assert index_path.exists()
    content = index_path.read_text(encoding="utf-8")
    assert "# HMN 机器索引" in content
    assert "- [docs-host](docs-host/README.md) — `managed` — `100.64.0.5`" in content


def test_docs_generate_runs_all_asset_indexes(tmp_path):
    db = tmp_path / "hmn.db"
    output_root = tmp_path / "docs"
    service_root = tmp_path / "service"
    runbook_root = tmp_path / "runbooks"
    runbook_root.mkdir()
    (runbook_root / "restore-mail.md").write_text("# Restore Mail\n", encoding="utf-8")
    SQLiteStore(db).save_node(_managed_node())
    CliRunner().invoke(
        app,
        [
            "docs",
            "service",
            "mailgw",
            "--service-root",
            str(service_root),
            "--title",
            "Mail Gateway",
            "--node",
            "docs-host",
            "--url",
            "https://mail.example.invalid",
        ],
    )

    result = CliRunner().invoke(
        app,
        [
            "docs",
            "generate",
            "--db",
            str(db),
            "--output-root",
            str(output_root),
            "--service-root",
            str(service_root),
            "--runbook-root",
            str(runbook_root),
        ],
    )

    assert result.exit_code == 0
    assert (output_root / "server" / "docs-host" / "README.md").exists()
    assert (output_root / "server" / "README.md").exists()
    assert (service_root / "README.md").exists()
    assert (service_root / "domains.md").exists()
    assert (service_root / "runbooks.md").exists()
    assert "- [Mail Gateway](mailgw/README.md) — `mailgw`" in (service_root / "README.md").read_text(encoding="utf-8")
    assert "- `mail.example.invalid` → [Mail Gateway](mailgw/README.md)" in (service_root / "domains.md").read_text(encoding="utf-8")
    assert "- [Restore Mail]" in (service_root / "runbooks.md").read_text(encoding="utf-8")
    assert "生成机器文档: 1" in result.stdout
    assert "已刷新服务索引" in result.stdout
    assert "已刷新域名索引" in result.stdout
    assert "已刷新 Runbook 索引" in result.stdout


def test_docs_service_generates_service_document(tmp_path):
    service_root = tmp_path / "service"

    result = CliRunner().invoke(
        app,
        [
            "docs",
            "service",
            "codex-registrar2",
            "--service-root",
            str(service_root),
            "--title",
            "Codex Registrar 2",
            "--node",
            "docs-host",
            "--url",
            "https://example.invalid/codex",
            "--port",
            "8080",
            "--summary",
            "Temporary account registration service",
        ],
    )

    assert result.exit_code == 0
    doc_path = service_root / "codex-registrar2" / "README.md"
    assert doc_path.exists()
    content = doc_path.read_text(encoding="utf-8")
    assert "# Codex Registrar 2" in content
    assert "服务 ID: `codex-registrar2`" in content
    assert "节点: `docs-host`" in content
    assert "URL: `https://example.invalid/codex`" in content
    assert "端口: `8080`" in content
    assert "Temporary account registration service" in content
    assert str(doc_path) in result.stdout


def test_docs_service_index_tracks_generated_services(tmp_path):
    service_root = tmp_path / "service"
    runner = CliRunner()
    created = runner.invoke(
        app,
        ["docs", "service", "mailgw", "--service-root", str(service_root), "--title", "Mail Gateway", "--node", "docs-host"],
    )
    assert created.exit_code == 0

    result = runner.invoke(app, ["docs", "service-index", "--service-root", str(service_root)])

    assert result.exit_code == 0
    index_path = service_root / "README.md"
    assert index_path.exists()
    content = index_path.read_text(encoding="utf-8")
    assert "# HMN 服务索引" in content
    assert "- [Mail Gateway](mailgw/README.md) — `mailgw`" in content


def test_docs_domain_index_collects_service_urls(tmp_path):
    service_root = tmp_path / "service"
    runner = CliRunner()
    created = runner.invoke(
        app,
        [
            "docs",
            "service",
            "mailgw",
            "--service-root",
            str(service_root),
            "--title",
            "Mail Gateway",
            "--url",
            "https://mail.example.invalid/login",
        ],
    )
    assert created.exit_code == 0

    result = runner.invoke(app, ["docs", "domain-index", "--service-root", str(service_root)])

    assert result.exit_code == 0
    index_path = service_root / "domains.md"
    assert index_path.exists()
    content = index_path.read_text(encoding="utf-8")
    assert "# HMN 域名索引" in content
    assert "- `mail.example.invalid` → [Mail Gateway](mailgw/README.md)" in content


def test_docs_runbook_index_collects_markdown_titles(tmp_path):
    service_root = tmp_path / "service"
    runbook_root = tmp_path / "runbooks"
    runbook_root.mkdir()
    (runbook_root / "restore-mail.md").write_text("# Restore Mail\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["docs", "runbook-index", "--service-root", str(service_root), "--runbook-root", str(runbook_root)])

    assert result.exit_code == 0
    index_path = service_root / "runbooks.md"
    assert index_path.exists()
    content = index_path.read_text(encoding="utf-8")
    assert "# HMN Runbook 索引" in content
    assert "- [Restore Mail]" in content
