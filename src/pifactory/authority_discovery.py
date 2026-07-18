from __future__ import annotations

from typing import Any


class AuthorityDiscoveryDisabled(RuntimeError):
    pass


def discover_authoritative_urls(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    raise AuthorityDiscoveryDisabled(
        "权威页面搜索发现已永久禁用。请在 profiles/<profile_id>/seed.yaml 的 "
        "authoritative_sources 中维护固定 HTTPS 页面。"
    )
