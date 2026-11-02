"""Reconcile legacy schema.sql installations with the ORM schema.

This revision is deliberately additive so existing data survives upgrades.  Fresh
databases already contain these objects through 0001 and therefore take the
idempotent no-op path.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_schema_reconciliation"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def _columns(inspector, table):
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "admins" in tables and "must_change_password" not in _columns(inspector, "admins"):
        op.add_column("admins", sa.Column("must_change_password", sa.Integer(), nullable=False, server_default="1"))
    if "instances" in tables:
        columns = _columns(inspector, "instances")
        if "last_desired_state" not in columns:
            op.add_column("instances", sa.Column("last_desired_state", sa.String(20), nullable=False, server_default="RUNNING"))
        if "error_message" not in columns:
            op.add_column("instances", sa.Column("error_message", sa.Text(), nullable=True))
    # Create only missing lifecycle tables; never rebuild or truncate legacy data.
    from app.core.database import Base
    from app.models import entities  # noqa: F401
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade():
    # Data-preserving downgrade: the additive compatibility fields remain.  A
    # destructive column/table removal requires an explicit backup-aware revision.
    pass
