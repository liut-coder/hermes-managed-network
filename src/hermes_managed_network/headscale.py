from __future__ import annotations

import os
from dataclasses import dataclass

from .network_base import JsonHttpClient, NetworkNodeRecord, NetworkProvider, NetworkProviderError, NetworkStatus, PreauthKeyResult


@dataclass
class HeadscaleProvider(NetworkProvider):
    base_url: str
    api_key_env: str
    user: str = "default"
    provider_name: str = "headscale"

    @classmethod
    def from_config(cls, network_config: dict[str, object]) -> "HeadscaleProvider":
        headscale = network_config.get("headscale")
        if not isinstance(headscale, dict):
            raise NetworkProviderError("缺少 network.headscale 配置")
        base_url = str(headscale.get("url") or "").rstrip("/")
        api_key_env = str(headscale.get("api_key_env") or "").strip()
        user = str(headscale.get("user") or "default").strip() or "default"
        if not base_url:
            raise NetworkProviderError("缺少 network.headscale.url")
        if not api_key_env:
            raise NetworkProviderError("缺少 network.headscale.api_key_env")
        return cls(base_url=base_url, api_key_env=api_key_env, user=user)

    @property
    def endpoint(self) -> str:
        return self.base_url

    def _client(self) -> JsonHttpClient:
        token = os.environ.get(self.api_key_env, "").strip()
        if not token:
            raise NetworkProviderError(f"缺少环境变量 {self.api_key_env}")
        return JsonHttpClient(self.base_url, token=token)

    def _normalize_node(self, raw: dict[str, object]) -> NetworkNodeRecord:
        provider_node_id = str(raw.get("id") or raw.get("nodeId") or "")
        hostname = str(raw.get("givenName") or raw.get("name") or raw.get("hostname") or provider_node_id)
        online = bool(raw.get("online") or raw.get("isOnline") or False)
        tags = [str(tag) for tag in (raw.get("forcedTags") or raw.get("tags") or [])]
        ip = ""
        for candidate in raw.get("ipAddresses") or raw.get("addresses") or []:
            value = str(candidate)
            if "." in value or ":" in value:
                ip = value
                break
        return NetworkNodeRecord(
            provider_node_id=provider_node_id,
            hostname=hostname,
            ip=ip,
            tags=tags,
            online=online,
            raw=raw,
        )

    def list_nodes(self) -> list[NetworkNodeRecord]:
        client = self._client()
        payload = client.request_json("GET", f"/api/v1/node{client.encode_query({'user': self.user})}")
        rows = payload.get("nodes") or payload.get("machines") or []
        if not isinstance(rows, list):
            raise NetworkProviderError("Headscale nodes 返回格式错误")
        return [self._normalize_node(row) for row in rows if isinstance(row, dict)]

    def status(self) -> NetworkStatus:
        nodes = self.list_nodes()
        return NetworkStatus(
            provider=self.provider_name,
            configured=True,
            endpoint=self.endpoint,
            node_count=len(nodes),
            online_count=sum(1 for node in nodes if node.online),
            nodes=nodes,
        )

    def create_preauth_key(
        self,
        *,
        node_id: str,
        tags: list[str],
        reusable: bool,
        ephemeral: bool,
        expiration: str | None,
    ) -> PreauthKeyResult:
        client = self._client()
        payload = {
            "user": self.user,
            "reusable": reusable,
            "ephemeral": ephemeral,
            "expiration": expiration,
            "aclTags": tags,
        }
        response = client.request_json("POST", "/api/v1/preauthkey", payload=payload)
        data = response.get("preAuthKey") or response.get("preauth_key") or response
        if not isinstance(data, dict):
            raise NetworkProviderError("Headscale preauth key 返回格式错误")
        key = str(data.get("key") or data.get("value") or "")
        if not key:
            raise NetworkProviderError("Headscale 未返回 preauth key")
        return PreauthKeyResult(
            key=key,
            reusable=bool(data.get("reusable", reusable)),
            ephemeral=bool(data.get("ephemeral", ephemeral)),
            expiration=str(data.get("expiration") or expiration or "") or None,
            tags=[str(tag) for tag in (data.get("aclTags") or tags)],
            raw=data,
        )
