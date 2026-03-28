"""
Shared URL safety utilities — used by both the scraper collector and the
media downloader to prevent SSRF (Server-Side Request Forgery).

`is_safe_url` rejects URLs that target private/link-local IPs, loopback
addresses, and cloud metadata endpoints.  It also resolves hostnames via
DNS so that hex/decimal IP representations and DNS-rebinding attacks are
caught before any outbound request is made.
"""
import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_HOSTS = {
    "localhost",
    "0.0.0.0",
    "::1",
    "0x7f000001",
    "metadata.google.internal",
}


def is_safe_url(url: str) -> bool:
    """Return False for URLs targeting private/link-local IPs (SSRF prevention).

    Checks performed:
    - Scheme must be http or https
    - Hostname must be non-empty and not on the explicit blocklist
    - If the hostname is a direct IP, it must not be private/link-local/loopback
    - DNS resolution is attempted so that hostnames resolving to private ranges
      are also rejected (catches hex/decimal IP representations and rebinding)

    Unresolvable hostnames are allowed through — the downstream HTTP client
    will fail on them.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname or ""
        if not host:
            return False
        # Explicit hostname blocklist (catches localhost, hex IPs, etc.)
        if host.lower() in _BLOCKED_HOSTS:
            return False
        if host == "169.254.169.254":
            return False
        # Direct IP check
        try:
            addr = ipaddress.ip_address(host)
            if addr.is_private or addr.is_link_local or addr.is_loopback:
                return False
        except ValueError:
            pass
        # DNS resolution check — catches hex/decimal IP representations
        # and hostnames that resolve to private IPs
        try:
            resolved = socket.gethostbyname(host)
            addr = ipaddress.ip_address(resolved)
            if addr.is_private or addr.is_link_local or addr.is_loopback:
                return False
        except (socket.gaierror, ValueError):
            pass  # unresolvable hostname — let it through, httpx will fail
        return True
    except Exception:
        return False
