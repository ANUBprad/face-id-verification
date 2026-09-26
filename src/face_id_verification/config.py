from __future__ import annotations

from pathlib import Path

from dotenv import find_dotenv, load_dotenv


def project_root() -> Path:
    """Directory owning the active interpreter, or the project checkout for editable installs."""
    return Path(__file__).resolve().parent.parent.parent


def find_local_env_file() -> Path | None:
    """Locate the project's own .env without adopting one from an unrelated directory."""
    root = project_root()

    anchored = root / ".env"
    if anchored.is_file():
        return anchored

    discovered = find_dotenv(usecwd=True)
    if discovered:
        candidate = Path(discovered).resolve()
        if root in candidate.parents:
            return candidate
    return None


def load_local_config() -> Path | None:
    """Load the project's local .env without replacing already-set variables."""
    env_file = find_local_env_file()
    if env_file is not None:
        load_dotenv(dotenv_path=env_file, override=False)
    return env_file
