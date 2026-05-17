from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, unquote

DEFAULT_KUMA_DB = Path("/opt/uptime-kuma/data/kuma.db")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


@dataclass(frozen=True)
class KumaServiceAsset:
    service_id: str
    name: str
    node_id: str
    kind: str
    runtime: str
    domains: list[str]
    ports: list[int]
    status: str
    monitor_enabled: bool
    docs_path: str
    source: str
    business_category: str
    asset_category: str
    asset_score: int
    why_asset: list[str]
    summary: str
    deployment_type: str
    project_name: str
    business_name: str
    business_purpose: str
    public_exposed: bool
    backup_status: str
    tags: list[str]
    raw: dict[str, Any]


def _normalize_port(parsed) -> int | None:
    if parsed.port:
        return int(parsed.port)
    if parsed.scheme == "https":
        return 443
    if parsed.scheme == "http":
        return 80
    return None


def _monitor_host(row: sqlite3.Row) -> str:
    url = str(row["url"] or "").strip()
    if url:
        parsed = urlparse(url)
        if parsed.hostname:
            return parsed.hostname
    hostname = str(row["hostname"] or "").strip()
    return hostname


def _monitor_ports(row: sqlite3.Row) -> list[int]:
    url = str(row["url"] or "").strip()
    if url:
        parsed = urlparse(url)
        port = _normalize_port(parsed)
        if port is not None:
            return [port]
    if row["port"] is not None:
        try:
            return [int(row["port"])]
        except (TypeError, ValueError):
            return []
    return []


def _monitor_domains(row: sqlite3.Row) -> list[str]:
    host = _monitor_host(row)
    if not host or host in LOCAL_HOSTS:
        return []
    return [host]


def _group_name(row: sqlite3.Row) -> str:
    name = str(row["group_name"] or "").strip()
    return name or "未分组"


def _asset_category(row: sqlite3.Row) -> str:
    host = _monitor_host(row).lower()
    group_name = _group_name(row).lower()
    description = str(row["description"] or "").lower()
    if host in LOCAL_HOSTS or "system" in group_name or "系统" in group_name or "system" in description or "系统" in description:
        return "system"
    if not bool(row["active"]):
        return "pending"
    return "main"


def _asset_score(row: sqlite3.Row, category: str) -> int:
    if category == "system":
        return 20
    if category == "pending":
        return 40
    score = 90
    if bool(row["keyword"]):
        score += 10
    return min(score, 100)


def _why_asset(row: sqlite3.Row, category: str) -> list[str]:
    reasons = [f"Uptime Kuma monitor #{row['id']}"]
    group_name = _group_name(row)
    if group_name != "未分组":
        reasons.append(f"group: {group_name}")
    reasons.append("active monitor" if bool(row["active"]) else "inactive monitor")
    reasons.append(f"{row['type'] or 'http'} check")
    if category == "system":
        reasons.append("local/system endpoint")
    return reasons[:5]


def _summary(row: sqlite3.Row) -> str:
    target = str(row["url"] or row["hostname"] or "").strip()
    return f"{row['name']} · {target}" if target else str(row["name"] or "")


def _pick_primary_group_rows(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Keep one row per monitor, preferring the highest-weight group mapping."""
    selected: dict[int, sqlite3.Row] = {}
    for row in rows:
        monitor_id = int(row["id"])
        if monitor_id not in selected:
            selected[monitor_id] = row
    return [selected[monitor_id] for monitor_id in sorted(selected)]


def load_kuma_service_assets(db_path: Path | str | None = None) -> list[KumaServiceAsset]:
    path = Path(db_path or DEFAULT_KUMA_DB).expanduser()
    if not path.exists():
        return []
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            '''
            SELECT
              m.id,
              m.name,
              m.active,
              m.url,
              m.type,
              m.keyword,
              m.hostname,
              m.port,
              m.description,
              mg.weight,
              g.name AS group_name
            FROM monitor m
            LEFT JOIN monitor_group mg ON mg.monitor_id = m.id
            LEFT JOIN "group" g ON g.id = mg.group_id
            WHERE COALESCE(m.parent, 0) = 0
            ORDER BY COALESCE(mg.weight, 0) DESC, m.id ASC
            '''
        ).fetchall()
    finally:
        con.close()

    assets: list[KumaServiceAsset] = []
    for row in _pick_primary_group_rows(rows):
        category = _asset_category(row)
        host = _monitor_host(row)
        domains = _monitor_domains(row)
        ports = _monitor_ports(row)
        group_name = _group_name(row)
        tags = ["uptime-kuma", str(row["type"] or "http")]
        if category == "system":
            tags.append("system")
        assets.append(
            KumaServiceAsset(
                service_id=f"kuma:{row['id']}",
                name=str(row["name"] or f"monitor-{row['id']}"),
                node_id="uptime-kuma",
                kind=str(row["type"] or "http"),
                runtime="uptime-kuma",
                domains=domains,
                ports=ports,
                status="active" if bool(row["active"]) else "paused",
                monitor_enabled=bool(row["active"]),
                docs_path="",
                source="uptime-kuma",
                business_category=group_name,
                asset_category=category,
                asset_score=_asset_score(row, category),
                why_asset=_why_asset(row, category),
                summary=_summary(row),
                deployment_type="uptime-kuma",
                project_name=group_name if group_name != "未分组" else "",
                business_name=str(row["name"] or ""),
                business_purpose="可用性监控",
                public_exposed=bool(domains),
                backup_status="unknown",
                tags=tags,
                raw={key: row[key] for key in row.keys()} | {"host": host},
            )
        )
    return assets
