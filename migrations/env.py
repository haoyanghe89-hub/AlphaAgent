from __future__ import annotations

import os

from alembic import context

from marketpulse.investigation.persistence import models as investigation_models  # noqa: F401
from marketpulse.investigation.persistence.base import Base, create_investigation_engine
from marketpulse.quant.knowledge import models as knowledge_models  # noqa: F401
from marketpulse.quant.storage import models as quant_models  # noqa: F401

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    explicit = config.attributes.get("database_url")
    if explicit is not None:
        return str(explicit)
    return os.getenv("MARKETPULSE_DATABASE_URL") or config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_investigation_engine(_database_url())
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
