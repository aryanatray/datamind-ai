"""Conversation scope/pins and immutable document upload versions."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "conversations", sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("conversations", sa.Column("scope", sa.JSON(), nullable=False, server_default="{}"))
    op.create_table(
        "document_versions",
        sa.Column("source_id", sa.String(36), sa.ForeignKey("sources.id"), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("extension", sa.String(10), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
    )
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO document_versions (source_id, version, filename, extension, path, sha256, size, created) "
            "SELECT documents.id, sources.version, documents.filename, documents.extension, "
            "documents.path, documents.sha256, documents.size, sources.created "
            "FROM documents JOIN sources ON documents.id=sources.id WHERE sources.status!='deleted'"
        )
    )


def downgrade():
    raise RuntimeError("Use a reviewed backup/restore procedure for destructive downgrades")
