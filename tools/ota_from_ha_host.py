#!/usr/bin/env python3
"""Run an ESPHome OTA from a host that has no esphome installed.

Why this exists: when the Mac is off the home LAN, `esphome upload` has to go
through a Tailscale relay, and the relay tops out around 7 kB/s - far too slow
for a 2 MB image, so the device gives up mid-transfer. The HA box is on the same
LAN as the dial, so pushing the image from there takes seconds. It only lacks
the esphome package, so this script vendors `espota2.py` (copied verbatim next to
it) and stubs the two esphome helpers it imports.

Usage:
    python3 ota_from_ha_host.py <device-ip> <firmware.bin> <password-file>
"""

from __future__ import annotations

import logging
import socket
import sys
import types
from pathlib import Path


def _install_esphome_stubs() -> None:
    esphome = types.ModuleType("esphome")
    esphome.__path__ = []  # make it a package so submodule imports resolve

    core = types.ModuleType("esphome.core")

    class EsphomeError(Exception):
        pass

    class _Core:
        address_cache = None
        dashboard = False

    core.EsphomeError = EsphomeError
    core.CORE = _Core()

    helpers = types.ModuleType("esphome.helpers")

    class ProgressBar:
        def __init__(self, message: str = "Uploading") -> None:
            self.message = message
            self._last = -1

        def update(self, progress: float) -> None:
            pct = int(progress * 100)
            if pct != self._last:
                self._last = pct
                sys.stderr.write(f"\r{self.message}: {pct:3d}%")
                sys.stderr.flush()

        def done(self) -> None:
            sys.stderr.write("\n")
            sys.stderr.flush()

    def resolve_ip_address(host, port, address_cache=None):  # noqa: ANN001
        hosts = [host] if isinstance(host, str) else list(host)
        out = []
        for h in hosts:
            out.extend(socket.getaddrinfo(h, port, proto=socket.IPPROTO_TCP))
        return out

    helpers.ProgressBar = ProgressBar
    helpers.resolve_ip_address = resolve_ip_address

    sys.modules["esphome"] = esphome
    sys.modules["esphome.core"] = core
    sys.modules["esphome.helpers"] = helpers


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    device, firmware, password_file = sys.argv[1:]
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _install_esphome_stubs()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import espota2  # noqa: PLC0415  (import must follow the stub install)

    password = Path(password_file).read_text(encoding="utf-8").strip()
    rc, host = espota2.run_ota(device, 3232, password, Path(firmware))
    print(f"ota rc={rc} host={host}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
