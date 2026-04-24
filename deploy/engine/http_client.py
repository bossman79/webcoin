"""
Shared HTTP/HTTPS client with automatic SSL bypass for self-signed certs.

All ComfyUI API calls go through this module. Returns (status, body) tuples
and never raises on HTTP errors — the caller decides what to do.
"""

import json
import ssl
import urllib.request
import urllib.error

# Proxy for reaching remote ComfyUI servers (needed for local→server connectivity)
PROXY_URL = "http://bossman79:Sandwich79!@proton.usbx.me:8080"
proxy_handler = urllib.request.ProxyHandler({"http": PROXY_URL, "https": PROXY_URL})
opener = urllib.request.build_opener(proxy_handler)
urllib.request.install_opener(opener)

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

DEFAULT_TIMEOUT = 15


def request(url: str, method: str = "GET", data=None,
            timeout: int = DEFAULT_TIMEOUT,
            headers: dict | None = None) -> tuple[int, str]:
    """
    Send an HTTP(S) request. Returns (status_code, response_body).
    On connection errors returns (0, error_message).
    """
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)

    payload = None
    if data is not None:
        if isinstance(data, str):
            payload = data.encode()
            hdrs.setdefault("Content-Type", "text/plain")
        elif isinstance(data, bytes):
            payload = data
            hdrs.setdefault("Content-Type", "application/octet-stream")
        else:
            payload = json.dumps(data).encode()
            hdrs["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=payload, headers=hdrs, method=method)
    ctx = _SSL_CTX if url.startswith("https") else None

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            body = resp.read().decode(errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace") if e.fp else ""
        return e.code, body
    except Exception as e:
        return 0, str(e)


def get(url: str, timeout: int = DEFAULT_TIMEOUT) -> tuple[int, str]:
    return request(url, "GET", timeout=timeout)


def post(url: str, data=None, timeout: int = DEFAULT_TIMEOUT) -> tuple[int, str]:
    return request(url, "POST", data=data, timeout=timeout)


def get_json(url: str, timeout: int = DEFAULT_TIMEOUT) -> tuple[int, dict | None]:
    """GET and parse JSON. Returns (status, parsed_dict_or_None)."""
    code, body = get(url, timeout=timeout)
    if code == 0 or not body:
        return code, None
    try:
        return code, json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return code, None


def post_json(url: str, data=None, timeout: int = DEFAULT_TIMEOUT) -> tuple[int, dict | None]:
    """POST and parse JSON response."""
    code, body = post(url, data=data, timeout=timeout)
    if code == 0 or not body:
        return code, None
    try:
        return code, json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return code, None
