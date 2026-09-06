from __future__ import annotations

from ipaddress import IPv6Address, IPv6Network, ip_address

from fastapi import Request


def client_address(request: Request) -> str:
    """Identify the caller for throttling purposes.

    X-Forwarded-For is deliberately ignored: it is attacker-controlled unless a
    trusted proxy overwrites it, and uvicorn already rewrites request.client for
    proxies named by --forwarded-allow-ips.
    """
    client = request.client
    if client is None:
        return "unknown"
    try:
        address = ip_address(client.host)
    except ValueError:
        # TestClient reports the literal "testclient" rather than an address.
        return client.host
    if isinstance(address, IPv6Address):
        # A single IPv6 allocation hands out 2**64 addresses, so throttling one
        # address at a time would be no throttle at all.
        return str(IPv6Network(f"{address}/64", strict=False).network_address)
    return str(address)
