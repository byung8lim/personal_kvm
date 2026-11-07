"""Add persistent L4/L7 definitions and NFS volume ownership.

Revision ID: 0007_managed_addons
Revises: 0006_dns_hostname
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT as MySQLBigInteger
revision = "0007_managed_addons"
down_revision = "0006_dns_hostname"
branch_labels = None
depends_on = None

def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "managed_lb_services" not in tables:
        op.create_table("managed_lb_services",
            sa.Column("id",sa.BigInteger(),primary_key=True,autoincrement=True),
            sa.Column("name",sa.String(length=40),unique=True,nullable=False),
            sa.Column("layer",sa.String(length=2),nullable=False),
            sa.Column("specification",sa.Text(),nullable=False),
            sa.Column("status",sa.String(length=20),nullable=False,server_default="DRAFT"),
            sa.Column("error_message",sa.Text()),
            sa.Column("created_at",sa.DateTime()))
    if "managed_nfs_volumes" not in tables:
        op.create_table("managed_nfs_volumes",
            sa.Column("id",sa.BigInteger(),primary_key=True,autoincrement=True),
            sa.Column("name",sa.String(length=63),unique=True,nullable=False),
            sa.Column("file_path",sa.String(length=500),nullable=False),
            sa.Column("status",sa.String(length=20),nullable=False,server_default="READY"),
            sa.Column("created_at",sa.DateTime()))
    if "managed_nfs_mounts" not in tables:
        instance_id_type = sa.BigInteger().with_variant(MySQLBigInteger(unsigned=True), "mysql")
        op.create_table("managed_nfs_mounts",
            sa.Column("id",sa.BigInteger(),primary_key=True,autoincrement=True),
            sa.Column("volume_id",sa.BigInteger(),sa.ForeignKey("managed_nfs_volumes.id"),nullable=False),
            sa.Column("instance_id",instance_id_type,sa.ForeignKey("instances.id"),nullable=False),
            sa.Column("target_path",sa.String(length=250),nullable=False),
            sa.Column("status",sa.String(length=20),nullable=False,server_default="PLANNED"))

def downgrade():
    raise RuntimeError("Non-destructive downgrade: restore DB backup and migrate forward")
