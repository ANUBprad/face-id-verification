from __future__ import annotations

import os
import sys

import uvicorn

from face_id_verification.config import load_local_config
from face_id_verification.web.app import WRITE_TOKEN_ENV, create_app
from face_id_verification.web.hosts import BIND_PORT_ENV, bind_address, is_loopback_host

EXPOSURE_WARNING = (
    "MukhdaX is about to listen on {host}, which is not a loopback address.\n"
    "  The web service will be reachable by other machines on this network.\n"
    "  Blockchain writes are not open: POST /api/verify with enable_blockchain=true\n"
    "  still requires the {token_env} credential and a trusted browser origin, and\n"
    "  requests carrying an unrecognised Host header are refused.\n"
    "  Verification is still expensive and unmetered to a caller, so this service is\n"
    "  not intended to be exposed casually to the public internet. Put a reverse proxy\n"
    "  with TLS and its own authentication in front of it."
)


def exposure_warning(host: str) -> str | None:
    """Warn when the configured bind address puts the service beyond loopback.

    The operator's choice is honoured: this only reports the consequence, it never
    refuses to start.
    """
    if is_loopback_host(host):
        return None
    return EXPOSURE_WARNING.format(host=host, token_env=WRITE_TOKEN_ENV)


def main() -> None:
    load_local_config()
    host = bind_address()
    port = int(os.environ.get(BIND_PORT_ENV, "8000"))

    warning = exposure_warning(host)
    if warning:
        print(warning, file=sys.stderr)

    uvicorn.run(create_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
