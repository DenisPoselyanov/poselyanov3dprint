"""Security helpers: SSRF protection, URL validation, static file allowlist."""

from __future__ import annotations

import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse

import config


def is_safe_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https"):
            return False
        host = (parsed.hostname or "").lower()
        if not host or host == "localhost":
            return False
        if host.endswith(".local") or host.endswith(".internal"):
            return False

        blocked_names = {
            "metadata.google.internal",
            "metadata.goog",
        }
        if host in blocked_names:
            return False

        try:
            addr_infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return False

        for info in addr_infos:
            ip = ipaddress.ip_address(info[4][0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
            ):
                return False
        return True
    except Exception:
        return False


def is_static_file_allowed(file_path: str) -> bool:
    if not file_path or ".." in file_path or file_path.startswith("/"):
        return False
    name = Path(file_path).name
    if name in config.STATIC_ALLOWED_FILES:
        return True
    allowed_suffixes = (".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2")
    return name.endswith(allowed_suffixes) and "/" not in file_path.replace("\\", "/")


def validate_stl_link(url: str) -> bool:
    if not url:
        return True
    parsed = urlparse(url.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
