"""Initial DataMind schema, frozen for reproducible upgrades."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # The snapshot is generated from the initial schema, not a reference to mutable application models.
    import json
    from pathlib import Path

    snapshot = json.loads((Path(__file__).parent / "0001_schema.json").read_text())
    types = {
        "string": sa.String,
        "text": sa.Text,
        "integer": sa.Integer,
        "boolean": sa.Boolean,
        "datetime": sa.DateTime,
        "json": sa.JSON,
    }
    for table in snapshot:
        columns = []
        for col in table["columns"]:
            dtype = types[col["type"]](col["length"]) if col.get("length") else types[col["type"]]()
            args = [sa.ForeignKey(col["foreign_key"])] if col.get("foreign_key") else []
            columns.append(
                sa.Column(col["name"], dtype, *args, primary_key=col["primary_key"], nullable=col["nullable"])
            )
        constraints = [sa.UniqueConstraint(*item) for item in table["unique"]]
        op.create_table(table["name"], *columns, *constraints)
        for column in table["indexes"]:
            op.create_index(f"ix_{table['name']}_{column}", table["name"], [column])


def downgrade():
    raise RuntimeError("Destructive downgrades require a reviewed backup-and-restore procedure")
