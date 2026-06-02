"""Internet connectivity check (stdlib only).

The bot depends on a live internet connection for everything that matters —
talking to the exchange, polling the cloud relay for TradingView signals, and
sending Telegram/desktop alerts. When the link drops, orders silently fail and
signals are missed, so we surface an explicit Online/Offline indicator.

``has_internet()`` opens a short TCP connection to a couple of highly reliable
hosts (Cloudflare/Google DNS on port 53, then their HTTPS ports as a fallback
for networks that block raw DNS). It never raises and returns quickly so it is
safe to poll from a background thread. We deliberately avoid an HTTP request so
a captive portal that answers port 443 but blocks real traffic is less likely
to read as "online".
"""

from __future__ import annotations

import socket

# (host, port) probes, tried in order. DNS first (cheap, rarely blocked), then
# HTTPS as a fallback for restrictive networks.
_PROBES = [
    ("1.1.1.1", 53),     # Cloudflare DNS
    ("8.8.8.8", 53),     # Google DNS
    ("1.1.1.1", 443),    # Cloudflare HTTPS
    ("8.8.8.8", 443),    # Google HTTPS
]


def has_internet(timeout: float = 2.0) -> bool:
    """Return True if any reliable host is reachable within ``timeout`` seconds."""
    for host, port in _PROBES:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def host_reachable(host: str, port: int = 443, timeout: float = 3.0) -> bool:
    """Return True if a specific host:port accepts a connection (e.g. an exchange)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# Public-IP echo services, tried in order. Each returns the caller's public IP
# as plain text. We never send any data — just read the response.
_IP_SERVICES = [
    "https://api.ipify.org",
    "https://ipv4.icanhazip.com",
    "https://ifconfig.me/ip",
    "https://checkip.amazonaws.com",
]


def public_ip(timeout: float = 5.0):
    """Return this machine's public (outbound) IP as a string, or None.

    This is the address the exchange sees — what you paste into an API key's
    IP whitelist. It is NOT the LAN address (192.168.x.x). Best-effort: tries a
    few reliable echo services and returns the first valid answer.
    """
    import ipaddress
    import urllib.request

    for url in _IP_SERVICES:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PrometheusBot"})
            ip = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8").strip()
            ipaddress.ip_address(ip)   # validates IPv4/IPv6; raises if junk
            return ip
        except Exception:  # noqa: BLE001 - try the next service
            continue
    return None

