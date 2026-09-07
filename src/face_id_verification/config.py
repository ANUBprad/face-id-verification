from __future__ import annotations

from dotenv import load_dotenv


def load_local_config() -> None:
    """Load optional local .env overrides without replacing set variables."""
    load_dotenv()