import requests
from urllib.parse import urljoin
from typing import Any, Dict
from pydantic import create_model
from framework.universes.data_models import (
    BaseUniverseModel,
    BaseUniverseParams,
)


def _normalise_collection(value: Any) -> Dict[str, Any] | None:
    """Convert the incoming collection to a dict.

    The `/info` endpoint returns `kbs` and `tbs` as empty ``[]`` when there are
    no entries.  `BaseUniverseParams` expects ``Dict[str, Any]`` (or ``None``),
    so we coerce ``list`` → ``dict`` and leave ``None`` untouched.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    # Anything else (e.g. an empty list) becomes an empty dict
    return {}


def get_universe_info(host: str, port: int, *, scheme: str = "http", timeout: int = 5) -> dict:
    """Fetch the `/info` endpoint of a running Universe.

    Parameters
    ----------
    host : str
        Hostname or IP address where the Universe server is listening.
    port : int
        TCP port for the FastAPI server.
    scheme : str, optional
        URL scheme – ``"http"`` (default) or ``"https"``.  The server started by
        ``run_app`` uses HTTP, but you can override if you have a reverse proxy.
    timeout : int, optional
        Seconds to wait for a response before raising ``requests.Timeout``.

    Returns
    -------
    dict
        The JSON payload returned by the ``/info`` endpoint, which includes
        ``node_info`` (the ``BaseUniverseModel`` data), the list of KBs, TBs, and
        the allowed actions.

    Raises
    ------
    requests.RequestException
        For network‑level errors (connection errors, timeouts, etc.).
    ValueError
        If the server response cannot be decoded as JSON.
    """
    # Normalise the host – strip any leading protocol bits the user might have added
    host = host.strip().lstrip("https://").lstrip("http://")
    base_url = f"{scheme}://{host}:{port}/"
    info_url = urljoin(base_url, "info")

    try:
        response = requests.get(info_url, timeout=timeout)
        response.raise_for_status()  # HTTP errors become exceptions
    except requests.RequestException as exc:
        raise requests.RequestException(
            f"Failed to contact Universe at {info_url}: {exc}"
        ) from exc

    try:
        return response.json()
    except ValueError as exc:
        raise ValueError(
            f"Response from {info_url} is not valid JSON: {exc}"
        ) from exc

# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------
#if __name__ == "__main__":
#    # Replace with the actual host/port of your running server
#    host = "0.0.0.0"
#    port = 8115
#    try:
#        info = get_universe_info(host, port)
#        print("Universe info:")
#        print(info)
#    except Exception as e:
#        print(f"Error: {e}")


def build_params_from_info(universe_info: Dict[str, Any]) -> BaseUniverseParams:
    """Create a ``BaseUniverseParams`` instance from the JSON payload of ``/info``.

    Parameters
    ----------
    universe_info: dict
        The complete dictionary returned by the ``/info`` endpoint.  Expected
        keys are ``node_info``, ``kbs`` and ``tbs``.

    Returns
    -------
    BaseUniverseParams
        An object that can be supplied directly to ``run_app``.
    """
    # ---------------------------------------------------------------------
    # 1️⃣ Extract the ``node_info`` block  values for the BaseUniverseModel.
    # ---------------------------------------------------------------------
    node_info = universe_info.get("node_info", {}) or {}

    # ---------------------------------------------------------------------
    # 2️⃣ Build a BaseUniverseModel *instance* from that dict.
    # ---------------------------------------------------------------------
    # ``BaseUniverseModel`` expects the fields ``name``, ``host``, ``port`` …
    # Any missing optional fields will take their default values.
    info_instance = BaseUniverseModel(**node_info)

    # ---------------------------------------------------------------------
    # 3️⃣ Normalise KB/TB collections  they should be dicts (or None).
    # ---------------------------------------------------------------------
    kbs = _normalise_collection(universe_info.get("kbs"))
    tbs = _normalise_collection(universe_info.get("tbs"))

    # ---------------------------------------------------------------------
    # 4️⃣ Assemble the params object.  ``info`` receives the *instance*.
    # ---------------------------------------------------------------------
    return BaseUniverseParams(info=info_instance, kbs=kbs, tbs=tbs)

def get_base_universe_params(host:str, port:int, verbose=0) -> BaseUniverseParams|None:
    try:
        info_dict = get_universe_info(host=host, port=port)
        params = build_params_from_info(info_dict)
        #info_dict = get_universe_info(host, port)
        #params = build_params_from_info(info_dict)
        if verbose > 0: print("BaseUniverseParams successfully built:\n", params)
    except Exception as exc:
        print(f"Failed to build params: {exc}")
        params = None
    return params
