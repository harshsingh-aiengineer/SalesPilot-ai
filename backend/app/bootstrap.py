"""Create folders, run migrations, and ensure singleton rows exist."""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import get_settings
from app.database import SessionLocal
from app.services.settings_store import ensure_defaults

logger = logging.getLogger(__name__)


def ensure_directories() -> None:
    settings = get_settings()
    settings.data_path.mkdir(parents=True, exist_ok=True)
    settings.secrets_path.mkdir(parents=True, exist_ok=True)
    if settings.database_url.startswith("sqlite:///./"):
        Path("data").mkdir(parents=True, exist_ok=True)


def run_migrations() -> None:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    command.upgrade(config, "head")


def ensure_seed_rows() -> None:
    db = SessionLocal()
    try:
        ensure_defaults(db)
        db.commit()
    finally:
        db.close()


def init_app() -> None:
    ensure_directories()
    run_migrations()
    ensure_seed_rows()
    settings = get_settings()
    if settings.using_default_password:
        logger.warning("SALESPILOT_PASSWORD is still the default. Change it before daily use.")
    if settings.using_default_secret:
        logger.warning("SALESPILOT_SECRET is still the default. Change it before daily use.")
