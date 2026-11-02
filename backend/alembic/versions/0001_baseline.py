"""Create the v0.0.6 baseline schema.

The installer may pre-create tables from schema.sql for compatibility. SQLAlchemy's
checkfirst behavior makes this migration idempotent for those installations while
allowing an empty database to be upgraded solely through Alembic.
"""
from alembic import op

from app.core.database import Base
from app.models import entities  # noqa: F401

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
