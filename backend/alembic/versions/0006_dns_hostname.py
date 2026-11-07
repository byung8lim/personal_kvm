"""Persist the VM's creation-time internal DNS name independently of guest hostname."""
from alembic import op
import sqlalchemy as sa

revision = "0006_dns_hostname"
down_revision = "0005_rocky_98_catalog"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("instances")}
    if "dns_hostname" not in columns:
        op.add_column("instances", sa.Column("dns_hostname", sa.String(64), nullable=True))
    op.execute("UPDATE instances SET dns_hostname = name WHERE dns_hostname IS NULL")
    op.alter_column("instances", "dns_hostname", existing_type=sa.String(64), nullable=False)
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("instances")}
    if "ix_instances_dns_hostname" not in indexes:
        op.create_index("ix_instances_dns_hostname", "instances", ["dns_hostname"], unique=False)


def downgrade():
    op.drop_index("ix_instances_dns_hostname", table_name="instances")
    op.drop_column("instances", "dns_hostname")
