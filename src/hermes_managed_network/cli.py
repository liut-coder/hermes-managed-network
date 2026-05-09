from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import typer

from .inventory import NodeRegistry
from .storage import SQLiteStore
from .tokens import JoinTokenStore

DEFAULT_DB = Path("~/.hmn/control-plane.db").expanduser()

app = typer.Typer(help="Hermes Managed Network control-plane CLI")
token_app = typer.Typer(help="Manage one-time join tokens")
node_app = typer.Typer(help="Manage registered nodes")
app.add_typer(token_app, name="token")
app.add_typer(node_app, name="node")


def _store(db: Path) -> SQLiteStore:
    return SQLiteStore(db)


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
    _store(db).save_token(token)
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
    typer.echo(f"revoked {token.value}")


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
    typer.echo(f"revoked {updated.node_id}")


if __name__ == "__main__":
    app()
