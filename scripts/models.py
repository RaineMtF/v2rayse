"""数据模型模块"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(eq=False)
class Proxy:
    """代理数据模型，支持基于 (type, ip, port) 的集合去重"""

    proxy_type: str
    ip: str
    port: int
    country: str = "Unknown"
    city: str = ""
    raw_str: str = ""
    category: str = ""

    @property
    def identity(self) -> tuple[str, str, int]:
        return (self.proxy_type, self.ip, self.port)

    @property
    def url(self) -> str:
        return f"{self.proxy_type}://{self.ip}:{self.port}"

    def __hash__(self) -> int:
        return hash(self.identity)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Proxy):
            return NotImplemented
        return self.identity == other.identity

    def __repr__(self) -> str:
        return f"<{self.proxy_type} {self.ip}:{self.port}>"


class ValidateResult:
    """代理验证结果"""

    def __init__(
        self,
        proxy: Proxy,
        available: bool,
        google_204_ms: Optional[float] = None,
        cloudflare_ms: Optional[float] = None,
        openssh_ok: bool = False,
        error: str = "",
        fail_step: str = "",
    ):
        self.proxy = proxy
        self.available = available
        self.google_204_ms = google_204_ms
        self.cloudflare_ms = cloudflare_ms
        self.openssh_ok = openssh_ok
        self.error = error
        self.fail_step = fail_step  # google / cloudflare / openssh / exception

    def __repr__(self) -> str:
        return (
            f"<ValidateResult {self.proxy} "
            f"available={self.available} "
            f"error={self.error!r}>"
        )
