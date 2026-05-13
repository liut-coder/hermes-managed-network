import json
from pathlib import Path

from typer.testing import CliRunner

from hermes_managed_network.cli import app


def test_docs_generate_reads_service_registry_and_writes_indexes(tmp_path):
    registry_path = tmp_path / "service-registry.json"
    output_dir = tmp_path / "docs-out"
    registry_path.write_text(
        json.dumps(
            {
                "services": [
                    {
                        "service_id": "node-a:docker:demo-web",
                        "name": "demo-web",
                        "node": "node-a",
                        "kind": "docker",
                        "runtime": "nginx:alpine",
                        "domains": ["example.com", "www.example.com"],
                        "ports": [80, 443],
                        "source": "docker inspect --format token=super-secret",
                        "docs_path": "service/demo-web.md",
                        "warnings": ["public edge"],
                        "monitor": {
                            "api_key": "top-secret",
                            "status": "ok",
                        },
                    }
                ]
            }
        )
    )

    result = CliRunner().invoke(
        app,
        [
            "docs",
            "generate",
            "--registry",
            str(registry_path),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    assert str(output_dir) in result.stdout

    service_doc = (output_dir / "service" / "demo-web.md").read_text()
    service_index = (output_dir / "service" / "README.md").read_text()
    domains_index = (output_dir / "domains" / "README.md").read_text()
    runbook_index = (output_dir / "runbooks" / "README.md").read_text()

    assert "# demo-web" in service_doc
    assert "- 承载节点：`node-a`" in service_doc
    assert "- 类型：`docker`" in service_doc
    assert "- 运行时：`nginx:alpine`" in service_doc
    assert "- 域名：`example.com`, `www.example.com`" in service_doc
    assert "- 端口：`80`, `443`" in service_doc
    assert "- 来源：`docker inspect --format [REDACTED]`" in service_doc
    assert "- 文档路径：`service/demo-web.md`" in service_doc
    assert "public edge" in service_doc
    assert "systemctl status <service>" in service_doc
    assert "top-secret" not in service_doc
    assert "super-secret" not in service_doc
    assert "[REDACTED]" in service_doc

    assert "- [demo-web](demo-web.md) · node-a · docker · example.com, www.example.com" in service_index
    assert "- `example.com` -> [demo-web](../service/demo-web.md)" in domains_index
    assert "- `www.example.com` -> [demo-web](../service/demo-web.md)" in domains_index
    assert "- [demo-web](../service/demo-web.md) · 节点 `node-a` · 查看常用运维命令占位" in runbook_index


def test_docs_generate_includes_docs_path_field_and_redacts_sensitive_keys(tmp_path):
    registry_path = tmp_path / "service-registry.json"
    output_dir = tmp_path / "docs-out"
    registry_path.write_text(
        json.dumps(
            {
                "services": [
                    {
                        "service_id": "node-b:systemd:db",
                        "name": "db",
                        "node": "node-b",
                        "kind": "systemd",
                        "runtime": "postgresql.service",
                        "domains": [],
                        "ports": [5432],
                        "source": (
                            "file:/etc/postgres env password=hunter2 token=abcd api_key=qwer "
                            "refresh_token=zzzz Authorization: Bearer Bearer123"
                        ),
                        "docs_path": "service/custom-db.md",
                        "warnings": [],
                        "monitor": {
                            "db_password": "hunter2",
                            "access_token": "secret-token",
                            "passwd": "pw-value",
                            "pwd": "pw-short",
                        },
                    }
                ]
            }
        )
    )

    result = CliRunner().invoke(
        app,
        [
            "docs",
            "generate",
            "--registry",
            str(registry_path),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0

    service_doc = (output_dir / "service" / "db.md").read_text()
    domains_index = (output_dir / "domains" / "README.md").read_text()

    assert "- 文档路径：`service/custom-db.md`" in service_doc
    assert "- 生成路径：`service/db.md`" in service_doc
    assert "hunter2" not in service_doc
    assert "abcd" not in service_doc
    assert "qwer" not in service_doc
    assert "zzzz" not in service_doc
    assert "secret-token" not in service_doc
    assert "Bearer123" not in service_doc
    assert "pw-value" not in service_doc
    assert "pw-short" not in service_doc
    assert service_doc.count("[REDACTED]") >= 8
    assert "暂无域名" in domains_index


def test_docs_generate_sanitizes_service_filename_and_keeps_output_inside_service_dir(tmp_path):
    registry_path = tmp_path / "service-registry.json"
    output_dir = tmp_path / "docs-out"
    registry_path.write_text(
        json.dumps(
            {
                "services": [
                    {
                        "service_id": "node-z:unknown:../../escape",
                        "name": "../../escape",
                        "node": "node-z",
                        "kind": "unknown",
                        "runtime": None,
                        "domains": [],
                        "ports": [],
                        "source": "manual",
                        "docs_path": None,
                        "warnings": [],
                    }
                ]
            }
        )
    )

    result = CliRunner().invoke(
        app,
        [
            "docs",
            "generate",
            "--registry",
            str(registry_path),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    assert (output_dir / "service" / "escape.md").exists()
    assert not (output_dir / "escape.md").exists()
    assert not (tmp_path / "escape.md").exists()
