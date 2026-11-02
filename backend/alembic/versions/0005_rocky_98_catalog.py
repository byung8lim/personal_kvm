"""Correct the Rocky 9 guest image catalog version to 9.8."""
from alembic import op


revision = "0005_rocky_98_catalog"
down_revision = "0004_instance_starting_state"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE os_images SET version='9.8' "
        "WHERE linux_type='ROCKY' AND version='9.5'"
    )


def downgrade():
    op.execute(
        "UPDATE os_images SET version='9.5' "
        "WHERE linux_type='ROCKY' AND version='9.8'"
    )
