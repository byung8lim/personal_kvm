"""Represent catalog entries whose image file has not been downloaded yet."""
from alembic import op
import sqlalchemy as sa

revision = "0003_image_missing_state"
down_revision = "0002_schema_reconciliation"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        op.execute(
            "ALTER TABLE os_images MODIFY status "
            "ENUM('MISSING','DOWNLOADING','READY','DELETING','DELETED','ERROR') "
            "NOT NULL DEFAULT 'MISSING'"
        )
    op.execute("UPDATE os_images SET status='MISSING' WHERE status='READY' AND size_mb=0")


def downgrade():
    op.execute("UPDATE os_images SET status='ERROR' WHERE status='MISSING'")
    if op.get_bind().dialect.name == "mysql":
        op.execute(
            "ALTER TABLE os_images MODIFY status "
            "ENUM('DOWNLOADING','READY','DELETING','DELETED','ERROR') "
            "NOT NULL DEFAULT 'READY'"
        )
