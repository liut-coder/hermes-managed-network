from pathlib import Path


def test_architecture_contract_documents_control_plane_boundaries():
    doc = Path("docs/architecture-contract.md").read_text()

    required_sections = [
        "## Layer ownership",
        "## Core invariants",
        "## Extension seams",
        "## Component lifecycle contract",
        "## Runtime profile contract",
        "## Network provider contract",
        "## Approval and audit contract",
    ]
    for section in required_sections:
        assert section in doc

    required_terms = [
        "Core MUST NOT hard-code nginx/frp/Headscale service details",
        "discover -> plan -> approve -> apply -> verify -> monitor -> upgrade/rollback/uninstall",
        "full-worker",
        "lite-worker",
        "beacon-only",
        "proxy-managed",
        "Network Provider Adapter",
        "Task Engine",
        "Component Bundle",
    ]
    for term in required_terms:
        assert term in doc


def test_architecture_index_links_to_contract():
    architecture = Path("docs/architecture.md").read_text()
    roadmap = Path("docs/roadmap.md").read_text()

    assert "architecture-contract.md" in architecture
    assert "架构契约" in roadmap


def test_v1_roadmap_is_closed_before_v1_1_planning():
    roadmap = Path("docs/roadmap.md").read_text()
    backlog = Path("docs/architecture-backlog.md").read_text()

    before_v1_1 = roadmap.split("## v1.1：全托管自动化规划", maxsplit=1)[0]
    assert "- [ ]" not in before_v1_1
    assert "- [x] 文档生成模板（已由 v0.6 资产文档自动化吸收" in roadmap
    assert "v1.0 主体闭环已完成，进入 v1.1 托管自动化建设" in backlog
