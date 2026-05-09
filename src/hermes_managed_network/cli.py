from __future__ import annotations

import json
import shlex
from datetime import timedelta
from pathlib import Path

import typer

from .executor import PlaybookExecutor
from .inventory import NodeRegistry
from .playbook import Playbook
from .storage import SQLiteStore
from .tokens import JoinTokenStore

DEFAULT_DB = Path("~/.hmn/control-plane.db").expanduser()
DEFAULT_PLAYBOOK_DIR = Path("playbooks")

app = typer.Typer(help="Hermes Managed Network control-plane CLI")
token_app = typer.Typer(help="Manage one-time join tokens")
node_app = typer.Typer(help="Manage registered nodes")
playbook_app = typer.Typer(help="Run local playbooks")
audit_app = typer.Typer(help="Inspect audit events")
app.add_typer(token_app, name="token")
app.add_typer(node_app, name="node")
app.add_typer(playbook_app, name="playbook")
app.add_typer(audit_app, name="audit")


def _store(db: Path) -> SQLiteStore:
    return SQLiteStore(db)


def _shell_quote(value: str) -> str:
    return shlex.quote(value)


def _render_join_command(token_value: str, master_url: str, user: str, safe: bool) -> str:
    base_url = master_url.rstrip("/")
    if safe:
        return (
            "tmp=$(mktemp) && curl -fsSL {url}/scripts/join.sh -o \"$tmp\" && "
            "sha256sum \"$tmp\" && sudo HERMES_JOIN_TOKEN={token} HERMES_MASTER_URL={url} HERMES_USER={user} bash \"$tmp\""
        ).format(
            token=_shell_quote(token_value),
            url=_shell_quote(base_url),
            user=_shell_quote(user),
        )
    return (
        "sudo HERMES_JOIN_TOKEN={token} HERMES_MASTER_URL={url} HERMES_USER={user} "
        "bash -s < <(curl -fsSL {url}/scripts/join.sh)"
    ).format(
        token=_shell_quote(token_value),
        url=_shell_quote(base_url),
        user=_shell_quote(user),
    )


def _parse_labels(labels_csv: str) -> list[str]:
    return [label.strip() for label in labels_csv.split(",") if label.strip()]


@app.command("menu")
def menu() -> None:
    typer.echo("HMN 快速菜单")
    typer.echo("1. wake")
    typer.echo("2. token create")
    typer.echo("3. token join-command")
    typer.echo("4. token list")
    typer.echo("5. node list")
    typer.echo("6. audit list")
    typer.echo("7. playbook run")
    typer.echo("")
    typer.echo("直接用子命令也可以，更适合自动化。")


@app.command("wake")
def wake(
    db: Path = typer.Option(DEFAULT_DB, "--db", help="SQLite database path"),
) -> None:
    """Interactively create a one-time join token and node bootstrap command."""
    hostname = typer.prompt("要接入的机器 hostname", default="s22900.dartnode.com")
    address = typer.prompt("机器 IP/地址", default="23.165.105.105")
    master_url = typer.prompt("主控 URL，例如 http://100.64.0.10:8765")
    trust_level = typer.prompt("信任级别 A/B/C", default="B").upper()
    labels_csv = typer.prompt("标签，逗号分隔", default="d2,worker,s22900")
    user = typer.prompt("节点系统用户", default="hermes")
    ttl_minutes = typer.prompt("token 有效期分钟", default=30, type=int)
    labels = _parse_labels(labels_csv)

    token_store = JoinTokenStore()
    token = token_store.create(
        trust_level=trust_level,
        labels=labels,
        ttl=timedelta(minutes=ttl_minutes),
    )
    store = _store(db)
    store.save_token(token)
    store.record_audit(
        event_type="token",
        subject_type="join_token",
        subject_id=token.value,
        action="create_for_wake",
        outcome="ok",
        details={
            "hostname": hostname,
            "address": address,
            "trust_level": token.trust_level,
            "labels": token.labels,
            "ttl_minutes": ttl_minutes,
        },
    )
    typer.echo("")
    typer.echo("唤醒脚本已生成")
    typer.echo(f"机器: {hostname}")
    typer.echo(f"地址: {address}")
    typer.echo(f"信任级别: {trust_level}")
    typer.echo("请复制下面这条命令到目标机器执行：")
    typer.echo(_render_join_command(token.value, master_url, user, safe=True))


@token_app.command("create")
def create_token(
    trust_level: str = typer.Option("B", "--trust", "-t", help="Trust level: A, B, or C"),
    label: list[str] = typer.Option([], "--label", "-l", help="Label to attach to the joining node"),
    ttl_minutes: int = typer.Option(30, "--ttl-minutes", help="Token lifetime in minutes"),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="SQLite database path"),
) -> None:
    token_store = JoinTokenStore()
    token = token_store.create(
        trust_level=trust_level,
        labels=list(label),
        ttl=timedelta(minutes=ttl_minutes),
    )
    store = _store(db)
    store.save_token(token)
    store.record_audit(
        event_type="token",
        subject_type="join_token",
        subject_id=token.value,
        action="create",
        outcome="ok",
        details={"trust_level": token.trust_level, "labels": token.labels, "ttl_minutes": ttl_minutes},
    )
    typer.echo(token.value)


@token_app.command("list")
def list_tokens(db: Path = typer.Option(DEFAULT_DB, "--db", help="SQLite database path")) -> None:
    for token in _store(db).list_tokens():
        typer.echo(f"{token.value}\t{token.status}\ttrust={token.trust_level}\tlabels={','.join(token.labels)}")


@token_app.command("revoke")
def revoke_token(
    token_value: str = typer.Argument(...),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="SQLite database path"),
) -> None:
    store = _store(db)
    token = store.load_token(token_value)
    if token is None:
        raise typer.Exit(1)
    token.status = "revoked"
    store.save_token(token)
    store.record_audit(
        event_type="token",
        subject_type="join_token",
        subject_id=token.value,
        action="revoke",
        outcome="ok",
        details={},
    )
    typer.echo(f"revoked {token.value}")


@token_app.command("join-command")
def join_command(
    token_value: str = typer.Argument(...),
    master_url: str = typer.Option(..., "--master-url", help="Master control-plane URL"),
    user: str = typer.Option("hermes", "--user", help="System user to create"),
    safe: bool = typer.Option(False, "--safe/--unsafe", help="Emit a safer download-and-run command"),
) -> None:
    typer.echo(_render_join_command(token_value, master_url, user, safe=safe))


@node_app.command("list")
def list_nodes(db: Path = typer.Option(DEFAULT_DB, "--db", help="SQLite database path")) -> None:
    for node in _store(db).list_nodes():
        typer.echo(f"{node.node_id}\t{node.status}\t{node.hostname}\ttrust={node.trust_level}")


@node_app.command("confirm")
def confirm_node(
    node_id: str = typer.Argument(...),
    bundle: list[str] = typer.Option(["observe"], "--bundle", "-b", help="Permission bundle to grant"),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="SQLite database path"),
) -> None:
    store = _store(db)
    node = store.load_node(node_id)
    if node is None:
        raise typer.Exit(1)
    registry = NodeRegistry({node.node_id: node})
    updated = registry.confirm(node_id, permission_bundles=list(bundle))
    if updated is None:
        raise typer.Exit(1)
    store.save_node(updated)
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id=updated.node_id,
        action="confirm",
        outcome="ok",
        details={"bundles": list(bundle)},
    )
    typer.echo(f"confirmed {updated.node_id}")


@node_app.command("revoke")
def revoke_node(
    node_id: str = typer.Argument(...),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="SQLite database path"),
) -> None:
    store = _store(db)
    node = store.load_node(node_id)
    if node is None:
        raise typer.Exit(1)
    registry = NodeRegistry({node.node_id: node})
    updated = registry.revoke(node_id)
    store.save_node(updated)
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id=updated.node_id,
        action="revoke",
        outcome="ok",
        details={},
    )
    typer.echo(f"revoked {updated.node_id}")


@playbook_app.command("run")
def run_playbook(
    file: Path = typer.Argument(..., exists=True, dir_okay=False),
    message: str = typer.Option(..., "--message", help="Input message for the playbook"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="Do not execute shell commands"),
) -> None:
    playbook = Playbook.load(file)
    run = PlaybookExecutor(dry_run=dry_run).run(playbook, values={"message": message})
    for result in run.results:
        typer.echo(f"{result.phase}: {result.command}")


@audit_app.command("list")
def list_audit_events(
    limit: int = typer.Option(50, "--limit", "-n", help="Maximum number of events to show"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON lines"),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="SQLite database path"),
) -> None:
    events = _store(db).list_audit_events()[-limit:]
    for event in events:
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "created_at": event.created_at.isoformat(),
                        "event_type": event.event_type,
                        "subject_type": event.subject_type,
                        "subject_id": event.subject_id,
                        "action": event.action,
                        "outcome": event.outcome,
                        "details": event.details,
                    },
                    sort_keys=True,
                )
            )
        else:
            details = json.dumps(event.details, ensure_ascii=False, sort_keys=True)
            typer.echo(
                f"{event.created_at.isoformat()}\t{event.event_type}\t{event.subject_type}\t"
                f"{event.subject_id}\t{event.action}\t{event.outcome}\t{details}"
            )


if __name__ == "__main__":
    app()
