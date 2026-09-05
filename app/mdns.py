"""Advertise the Inventory Hub on the local network via mDNS/Bonjour.

Devices resolve the hub by name (``inventoryhub.local``) instead of a hardcoded
IP, so a manager laptop that gets a new DHCP address doesn't strand its devices.
We also advertise an ``_http._tcp`` service so the hub could be browsed by name.

This is best-effort: if the ``zeroconf`` package isn't installed (it's an extra,
not a hard dependency) or registration fails, the app logs and runs fine — the
compiled-in fallback host in the firmware still works.
"""

from __future__ import annotations

import socket
from typing import Optional

HOSTNAME = "inventoryhub.local."
# Generic HTTP service (nice for browsing) plus a dedicated discovery service the
# firmware queries by name (_invhub._tcp) so it finds THIS hub unambiguously and
# adapts automatically when the computer's IP changes.
SERVICE_TYPE = "_http._tcp.local."
SERVICE_NAME = "Inventory Hub._http._tcp.local."
DISCOVERY_TYPE = "_invhub._tcp.local."
DISCOVERY_NAME = "Inventory Hub._invhub._tcp.local."

_zc = None            # active Zeroconf instance
_infos: list = []     # registered ServiceInfo objects

# Why devices can or can't find us. The packaged app runs with no console
# (console=False in the PyInstaller spec), so a printed warning goes nowhere —
# this is surfaced through /api/about and shown in the dashboard footer instead.
# A hub that isn't advertising is broken for this product, not degraded: every
# device configured with hub host "auto" depends on it.
_status: dict = {"ok": False, "reason": "not started yet", "ip": None, "port": None}


def status() -> dict:
    """Current advertisement state, for the UI and support diagnostics."""
    return dict(_status)


def _fail(reason: str) -> None:
    _status.update(ok=False, reason=reason)
    print(f"[mdns] NOT ADVERTISING: {reason}")


def _primary_lan_ip() -> Optional[str]:
    """Best guess at this machine's primary LAN IPv4 address.

    Opening a UDP socket toward a public IP doesn't send anything but makes the
    OS pick the outbound interface, whose address is the one devices can reach.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def start(port: int) -> None:
    """Register the hub's mDNS hostname + services. Safe to call once at startup."""
    global _zc, _infos
    if _zc is not None:
        return
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:
        # In a packaged build this means the bundler dropped zeroconf — see the
        # collect_all("zeroconf") call in inventory_hub.spec.
        _fail("zeroconf is missing from this build; devices cannot discover the hub")
        return

    ip = _primary_lan_ip()
    if not ip:
        _fail("no LAN IP found; is this machine connected to a network?")
        return

    try:
        _zc = Zeroconf()
        addr = [socket.inet_aton(ip)]
        _infos = [
            ServiceInfo(SERVICE_TYPE, SERVICE_NAME, addresses=addr, port=port,
                        properties={"path": "/"}, server=HOSTNAME),
            # Dedicated service the firmware discovers by name.
            ServiceInfo(DISCOVERY_TYPE, DISCOVERY_NAME, addresses=addr, port=port,
                        properties={"role": "inventory-hub"}, server=HOSTNAME),
        ]
        for info in _infos:
            _zc.register_service(info)
        _status.update(ok=True, reason="advertising", ip=ip, port=port)
        print(f"[mdns] advertising http://{HOSTNAME.rstrip('.')}:{port} at {ip} "
              f"(+ _invhub._tcp for device discovery)")
    except Exception as exc:  # noqa: BLE001 - never let mDNS crash startup
        # Several zeroconf exceptions (NonUniqueNameException among them) carry
        # an empty message, so fall back to the class name — "advertisement
        # failed:" with nothing after it tells a support caller nothing.
        detail = str(exc) or type(exc).__name__
        if type(exc).__name__ == "NonUniqueNameException":
            detail += " (another Inventory Hub is already advertising on this network)"
        _fail(f"advertisement failed: {detail}")
        stop()


def stop() -> None:
    """Unregister and close. Safe to call even if start() no-oped."""
    global _zc, _infos
    try:
        if _zc is not None:
            for info in _infos:
                try:
                    _zc.unregister_service(info)
                except Exception:  # noqa: BLE001
                    pass
            _zc.close()
    except Exception:  # noqa: BLE001
        pass
    finally:
        _zc = None
        _infos = []
