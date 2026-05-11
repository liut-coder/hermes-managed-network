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


def test_docs_generate_runs_server_docs_and_index(tmp_path):
    db = tmp_path / "hmn.db"
    output_root = tmp_path / "docs"
    SQLiteStore(db).save_node(_managed_node())

    result = CliRunner().invoke(app, ["docs", "generate", "--db", str(db), "--output-root", str(output_root)])

    assert result.exit_code == 0
    assert (output_root / "server" / "docs-host" / "README.md").exists()
    assert (output_root / "server" / "README.md").exists()
    assert "生成机器文档: 1" in result.stdout
