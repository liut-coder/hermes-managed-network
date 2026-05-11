from __future__ import annotations

from pathlib import Path


def test_production_readiness_doc_covers_v1_operator_checklist():
    doc_path = Path("docs/production-readiness.md")
    assert doc_path.exists()
    doc = doc_path.read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")
    deployment = Path("docs/deployment.md").read_text(encoding="utf-8")
    headscale = Path("docs/headscale-integration.md").read_text(encoding="utf-8")
    roadmap = Path("docs/roadmap.md").read_text(encoding="utf-8")

    for text in [readme, deployment, headscale]:
        assert "raw.githubusercontent.com/liut-coder/hermes-managed-network/main/install.sh" in text
        assert "feat/control-plane-mvp/install.sh" not in text

    assert "Production Readiness" in doc
    assert "hmn doctor" in doc
    assert "HTTPS" in doc
    assert "反代" in doc
    assert "防火墙" in doc
    assert "systemd" in doc
    assert "DB" in doc
    assert "env" in doc
    assert "config.yaml" in doc
    assert "metadata" in doc
    assert "Telegram approval gateway" in doc
    assert "Headscale provider" in doc
    assert "Worker protocol" in doc
    assert "/healthz" in doc
    assert "/api/v1/version" in doc
    assert "upgrade-manifest.env" in doc
    assert "rollback command" in doc
    assert "docs/production-readiness.md" in readme
    assert "生产 readiness checklist" in roadmap
