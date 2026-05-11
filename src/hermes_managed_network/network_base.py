from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from urllib import error, parse, request

import yaml


class NetworkProviderError(RuntimeError):
    pass


@dataclass
class NetworkNodeRecord:
    provider_node_id: str
    hostname: str
    ip: str
    tags: list[str] = field(default_factory=list)
    online: bool = False
    raw: dict[str, object] = field(default_factory=dict)


@dataclass
class NetworkStatus:
    provider: str
    configured: bool
    endpoint: str
    node_count: int
    online_count: int
    nodes: list[NetworkNodeRecord] = field(default_factory=list)


@dataclass
class PreauthKeyResult:
    key: str
    reusable: bool
    ephemeral: bool
    expiration: str | None
    tags: list[str] = field(default_factory=list)
    raw: dict[str, object] = field(default_factory=dict)


@dataclass
class NetworkSyncResult:
    provider: str
    linked: int
    updated: int
    unmatched: list[str] = field(default_factory=list)


class NetworkProvider(ABC):
    provider_name: str

    @abstractmethod
    def status(self) -> NetworkStatus:
        raise NotImplementedError

    @abstractmethod
    def list_nodes(self) -> list[NetworkNodeRecord]:
        raise NotImplementedError

    @abstractmethod
    def create_preauth_key(
        self,
        *,
        node_id: str,
        tags: list[str],
        reusable: bool,
        ephemeral: bool,
        expiration: str | None,
    ) -> PreauthKeyResult:
        raise NotImplementedError

    @abstractmethod
    def set_node_tags(self, provider_node_id: str, tags: list[str]) -> NetworkNodeRecord:
        raise NotImplementedError


def load_network_config(config_path: str | Path | None = None) -> dict[str, object]:
    candidates: list[Path] = []
    if config_path is not None:
        candidates.append(Path(config_path).expanduser())
    env_path = os.environ.get("HMN_CONFIG")
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.extend(
        [
            Path("/etc/hermes-managed-network/config.yaml"),
            Path("~/.hmn/config.yaml").expanduser(),
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise NetworkProviderError(f"配置文件格式错误: {candidate}")
            return data
    return {}


def get_network_provider(config_path: str | Path | None = None) -> NetworkProvider | None:
    config = load_network_config(config_path)
    network = config.get("network")
    if not isinstance(network, dict):
        return None
    provider = str(network.get("provider") or "").strip().lower()
    if not provider:
        return None
    if provider != "headscale":
        raise NetworkProviderError(f"暂不支持的 network provider: {provider}")
    from .headscale import HeadscaleProvider

    return HeadscaleProvider.from_config(network)


class JsonHttpClient:
    def __init__(self, base_url: str, *, token: str, timeout: int = 10) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request_json(self, method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        url = self.base_url + path
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        body: bytes | None = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=body, headers=headers, method=method.upper())
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise NetworkProviderError(f"Headscale API 请求失败: {exc.code} {detail}") from exc
        except error.URLError as exc:
            raise NetworkProviderError(f"Headscale API 不可达: {exc.reason}") from exc
        data = json.loads(raw or "{}")
        if not isinstance(data, dict):
            raise NetworkProviderError("Headscale API 返回了非对象 JSON")
        return data

    @staticmethod
    def encode_query(params: dict[str, str]) -> str:
        filtered = {key: value for key, value in params.items() if value != ""}
        if not filtered:
            return ""
        return "?" + parse.urlencode(filtered)
