from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import requests

from framework.universes.status_files import apply_status_to_infra, endpoint_from_status, read_status_file


@dataclass
class UniverseEndpointResolution:
    ok: bool
    name: str
    base_url: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    repaired: bool = False
    source: str = "unknown"
    error: Optional[str] = None


def _valid_port(value: Any) -> int | None:
    try:
        port = int(value)
    except Exception:
        return None
    return port if port > 0 else None


def _base_url_from_univ(univ: Any) -> str | None:
    info = getattr(univ, "info", None)
    port = _valid_port(getattr(info, "port", None)) if info is not None else None
    host = getattr(info, "host", None) if info is not None else None

    # Preferred path: BaseUniverseParams/BaseUniverseModel-style objects with
    # explicit host + positive port. Never treat port 0 as usable.
    if info is not None:
        if host and port is not None:
            try:
                return univ.get_base_url()
            except Exception:
                return f"http://{host}:{port}"
        # Important: if an object has explicit info but port is missing/0, do
        # not fall back to get_base_url(); doing so recreates the stale port-0
        # bug and prevents status-file/deployment-metadata repair.
        return None

    # Compatibility path for tests and older lightweight registry objects that
    # expose only get_base_url() and no structured .info.
    try:
        url = univ.get_base_url()
    except Exception:
        return None
    return str(url) if url else None


def resolve_universe_endpoint(infra: Any, name: str, *, repair: bool = True, verify: bool = False, timeout: float = 3.0) -> UniverseEndpointResolution:
    """Resolve and optionally repair a Universe HTTP endpoint.

    Resolution order:
    1. Existing infra.UNIVs[name].info when it has a positive port.
    2. managed_deployments[name].status_file ready/running status JSON.
    3. managed_deployments[name].endpoint or meta_data endpoint fields.

    This intentionally does not yet perform PID socket discovery; that remains a
    later constrained fallback after the status-file path is stable.
    """
    try:
        univ = getattr(infra, "UNIVs", {}).get(name)
    except Exception:
        univ = None
    if univ is None:
        return UniverseEndpointResolution(ok=False, name=name, error=f"Universe {name!r} is not registered")

    base_url = _base_url_from_univ(univ)
    if base_url:
        if verify:
            try:
                requests.get(f"{base_url}/health", timeout=timeout).raise_for_status()
            except Exception as exc:
                # A positive endpoint exists but health failed. Continue to status-file
                # repair in case the registry is stale.
                last_error = str(exc)
            else:
                info = getattr(univ, "info", None)
                if info is not None:
                    return UniverseEndpointResolution(ok=True, name=name, base_url=base_url, host=getattr(info, "host", None), port=_valid_port(getattr(info, "port", None)), source="infra.UNIVs")
                return UniverseEndpointResolution(ok=True, name=name, base_url=base_url, source="get_base_url")
        else:
            info = getattr(univ, "info", None)
            if info is not None:
                return UniverseEndpointResolution(ok=True, name=name, base_url=base_url, host=getattr(info, "host", None), port=_valid_port(getattr(info, "port", None)), source="infra.UNIVs")
            # Compatibility for legacy/lightweight objects that expose only
            # get_base_url(), e.g. test doubles or pre-refactor registry entries.
            return UniverseEndpointResolution(ok=True, name=name, base_url=base_url, source="get_base_url")
    else:
        last_error = "Universe endpoint has missing or non-positive port"

    deployments = getattr(infra, "managed_deployments", {}) or {}
    entry = deployments.get(name) if isinstance(deployments, dict) else None
    if isinstance(entry, dict):
        status = read_status_file(entry.get("status_file"))
        endpoint = endpoint_from_status(status)
        if endpoint:
            repaired = apply_status_to_infra(infra, name, status) if repair else False
            if verify:
                try:
                    requests.get(f"{endpoint['url']}/health", timeout=timeout).raise_for_status()
                except Exception as exc:
                    return UniverseEndpointResolution(ok=False, name=name, error=f"Resolved endpoint failed health check: {exc}", repaired=repaired, source="status_file")
            return UniverseEndpointResolution(ok=True, name=name, base_url=endpoint["url"], host=endpoint["host"], port=endpoint["port"], repaired=repaired, source="status_file")

        for container in (entry.get("endpoint"), entry.get("meta_data")):
            if isinstance(container, dict):
                port = _valid_port(container.get("port") or container.get("actual_port"))
                host = container.get("host") or container.get("remote_host") or getattr(getattr(univ, "info", None), "host", None) or "127.0.0.1"
                if port:
                    scheme = container.get("scheme") or "http"
                    url = container.get("url") or f"{scheme}://{host}:{port}"
                    if repair:
                        try:
                            univ.info.host = host
                            univ.info.port = port
                        except Exception:
                            pass
                    return UniverseEndpointResolution(ok=True, name=name, base_url=url, host=str(host), port=port, repaired=repair, source="deployment_metadata")

    return UniverseEndpointResolution(ok=False, name=name, error=last_error)


def get_universe_base_url_or_error(infra: Any, name: str) -> tuple[str | None, str | None, UniverseEndpointResolution]:
    resolution = resolve_universe_endpoint(infra, name, repair=True, verify=False)
    if resolution.ok and resolution.base_url:
        return resolution.base_url, None, resolution
    return None, resolution.error or "Unable to resolve Universe endpoint", resolution
