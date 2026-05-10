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
