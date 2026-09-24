"""The deployment a live probe measures against.

The suite is hermetic by design: ``tests/conftest.py`` deletes every ambient
``KODEZART_`` variable at import and unbinds the working-directory dotenv,
so the gate can never read a developer's live deployment.  A probe that
measures a DEPLOYMENT therefore names the file it measures against — the
``.env`` at the repository root — and reads it here and nowhere else.
"""

from pathlib import Path

from kodezart.config.app import AppConfig

DEPLOYMENT_ENV = Path(__file__).resolve().parents[2] / ".env"


def deployment_config() -> AppConfig | None:
    """The deployment's configuration, or ``None`` when no deployment file is there."""
    if not DEPLOYMENT_ENV.is_file():
        return None
    return AppConfig(_env_file=DEPLOYMENT_ENV)
