from __future__ import annotations

import os

import uvicorn

from face_id_verification.web.app import create_app


def main() -> None:
    host = os.environ.get("FACE_ID_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("FACE_ID_WEB_PORT", "8000"))
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()