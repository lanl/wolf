from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Iterable, Optional
from urllib.parse import urlparse


DEFAULT_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
}
DEFAULT_BLOCKED_IPS = {
    "169.254.169.254",  # cloud metadata
    "100.100.100.200",  # Alibaba metadata
}
DEFAULT_ALLOWED_SCHEMES = {"http", "https"}


@dataclass
class CapturePolicy:
    allowed_schemes: set[str] = field(default_factory=lambda: set(DEFAULT_ALLOWED_SCHEMES))
    blocked_hosts: set[str] = field(default_factory=lambda: set(DEFAULT_BLOCKED_HOSTS))
    blocked_ips: set[str] = field(default_factory=lambda: set(DEFAULT_BLOCKED_IPS))
    allow_private_networks: bool = False
    allow_localhost: bool = False
    max_url_length: int = 4096


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str = "ok"
    normalized_url: Optional[str] = None
    host: Optional[str] = None
    resolved_ips: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "normalized_url": self.normalized_url,
            "host": self.host,
            "resolved_ips": list(self.resolved_ips),
        }


def _is_ip_blocked(ip_text: str, policy: CapturePolicy) -> Optional[str]:
    if ip_text in policy.blocked_ips:
        return "blocked metadata/sensitive IP"
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return None
    if ip.is_loopback and not policy.allow_localhost:
        return "localhost/loopback targets are blocked"
    if (ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast) and not policy.allow_private_networks:
        return "private/link-local/reserved network targets are blocked"
    return None


def _resolve_host(host: str) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except Exception:
        return ()
    ips: list[str] = []
    for info in infos:
        try:
            addr = info[4][0]
        except Exception:
            continue
        if addr not in ips:
            ips.append(addr)
    return tuple(ips)


def validate_capture_url(url: str, policy: CapturePolicy | None = None) -> PolicyDecision:
    policy = policy or CapturePolicy()
    raw = str(url or "").strip()
    if not raw:
        return PolicyDecision(False, "URL is required")
    if len(raw) > policy.max_url_length:
        return PolicyDecision(False, f"URL is too long; max {policy.max_url_length} characters")
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in policy.allowed_schemes:
        return PolicyDecision(False, f"URL scheme '{scheme or '<missing>'}' is not allowed")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return PolicyDecision(False, "URL host is required")
    if host in policy.blocked_hosts:
        return PolicyDecision(False, f"host '{host}' is blocked", host=host)
    try:
        ipaddress.ip_address(host)
        host_is_ip = True
    except ValueError:
        host_is_ip = False
    resolved_ips = (host,) if host_is_ip else _resolve_host(host)
    if not resolved_ips and not host_is_ip:
        # DNS failures should be returned as capture-time errors rather than policy allow bypass.
        return PolicyDecision(True, "ok_dns_unresolved", normalized_url=raw, host=host, resolved_ips=())
    for ip in resolved_ips:
        reason = _is_ip_blocked(ip, policy)
        if reason:
            return PolicyDecision(False, reason, normalized_url=raw, host=host, resolved_ips=resolved_ips)
    return PolicyDecision(True, "ok", normalized_url=raw, host=host, resolved_ips=resolved_ips)
