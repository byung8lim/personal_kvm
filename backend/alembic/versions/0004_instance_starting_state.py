"""Allow the transient STARTING instance state used by lifecycle actions."""
from alembic import op


revision = "0004_instance_starting_state"
down_revision = "0003_image_missing_state"
branch_labels = None
depends_on = None


INSTANCE_STATES = (
    "'CREATING','STARTING','RUNNING','STOPPING','STOPPED',"
    "'RESIZING','DELETING','DELETED','ERROR'"
)


def upgrade():
    if op.get_bind().dialect.name == "mysql":
        op.execute(
            "ALTER TABLE instances MODIFY state "
            f"ENUM({INSTANCE_STATES}) NOT NULL DEFAULT 'CREATING'"
        )


def downgrade():
    # STARTING is transient; make any interrupted rows recoverable before
    # removing the enum member.
    op.execute("UPDATE instances SET state='STOPPED' WHERE state='STARTING'")
    if op.get_bind().dialect.name == "mysql":
        states = (
            "'CREATING','RUNNING','STOPPING','STOPPED',"
            "'RESIZING','DELETING','DELETED','ERROR'"
        )
        op.execute(
            "ALTER TABLE instances MODIFY state "
            f"ENUM({states}) NOT NULL DEFAULT 'CREATING'"
        )
