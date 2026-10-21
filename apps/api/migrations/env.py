from alembic import context
from datamind.db import Base, engine


def run():
    if context.is_offline_mode():
        raise RuntimeError("Use online migrations so the configured dialect is verified")
    with engine().connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


run()
