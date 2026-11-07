from sqlalchemy import Column, BigInteger, String, Integer, Numeric, DateTime, Text, ForeignKey
from sqlalchemy.dialects.mysql import BIGINT as MySQLBigInteger
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.database import Base

class AdminUser(Base):
    __tablename__ = "admins"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    must_change_password = Column(Integer, default=1, nullable=False)

class OsImage(Base):
    __tablename__ = "os_images"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    linux_type = Column(String(20), nullable=False)
    version = Column(String(20), nullable=False)
    image_format = Column(String(15), default="qcow2")
    size_mb = Column(Integer, default=0)
    source_url = Column(String(500), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    default_user = Column(String(30), default="rocky")
    root_partition = Column(String(30), default="/dev/sda4")
    checksum_sha256 = Column(String(64), nullable=True)
    released_at = Column(DateTime, nullable=True)
    registered_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(20), default="READY")

class StoragePool(Base):
    __tablename__ = "storage_pools"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    pool_name = Column(String(50), unique=True, nullable=False)
    mount_path = Column(String(255), nullable=False)
    pool_type = Column(String(20), nullable=False)
    total_size_gb = Column(Numeric(10,2), default=500.0)
    allocated_size_gb = Column(Numeric(10,2), default=0.0)
    available_size_gb = Column(Numeric(10,2), default=500.0)

class VirtualNetwork(Base):
    __tablename__ = "virtual_networks"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    net_name = Column(String(50), unique=True, nullable=False)
    net_type = Column(String(20), nullable=False)
    bridge_name = Column(String(20), unique=True, nullable=False)
    cidr = Column(String(25), nullable=False)
    gateway_ip = Column(String(15), nullable=True)
    dns_server = Column(String(15), default="8.8.8.8")
    ip_start = Column(String(15), nullable=False)
    ip_end = Column(String(15), nullable=False)
    total_ips = Column(Integer, default=100)
    assigned_ips = Column(Integer, default=0)

class IpPool(Base):
    __tablename__ = "ip_pools"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    network_id = Column(BigInteger, ForeignKey("virtual_networks.id", ondelete="CASCADE"), nullable=False)
    ip_address = Column(String(15), unique=True, nullable=False)
    status = Column(String(20), default="AVAILABLE")
    instance_id = Column(BigInteger, ForeignKey("instances.id", ondelete="SET NULL"), nullable=True)
    assigned_at = Column(DateTime, nullable=True)

class Instance(Base):
    __tablename__ = "instances"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    instance_uuid = Column(String(36), unique=True, nullable=False)
    name = Column(String(64), unique=True, nullable=False)
    dns_hostname = Column(String(64), nullable=False)
    os_image_id = Column(BigInteger, ForeignKey("os_images.id"), nullable=False)
    vcores = Column(Integer, default=2)
    memory_gb = Column(Integer, default=4)
    root_disk_gb = Column(Integer, default=20)
    extra_disk_gb = Column(Integer, default=0)
    public_ip = Column(String(15), nullable=True)
    private_ip = Column(String(15), nullable=True)
    mgmt_ip = Column(String(15), nullable=True)
    state = Column(String(20), default="RUNNING")
    last_desired_state = Column(String(20), default="RUNNING")
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class InstanceNic(Base):
    __tablename__ = "instance_nics"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    instance_id = Column(BigInteger, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False)
    network_id = Column(BigInteger, ForeignKey("virtual_networks.id"), nullable=False)
    ip_pool_id = Column(BigInteger, ForeignKey("ip_pools.id"), unique=True, nullable=False)
    iface_name = Column(String(10), nullable=False)
    mac_address = Column(String(17), unique=True, nullable=False)
    is_default_gw = Column(Integer, default=0)

class VirtualDisk(Base):
    __tablename__ = "virtual_disks"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    disk_uuid = Column(String(36), unique=True, nullable=False)
    disk_name = Column(String(100), nullable=False)
    pool_id = Column(BigInteger, ForeignKey("storage_pools.id"), nullable=False)
    disk_role = Column(String(20), nullable=False)
    size_gb = Column(Integer, default=20)
    file_path = Column(String(500), nullable=False)
    is_assigned = Column(Integer, default=0)
    instance_id = Column(BigInteger, ForeignKey("instances.id", ondelete="SET NULL"), nullable=True)
    target_dev = Column(String(10), nullable=True)
    status = Column(String(20), default="AVAILABLE")
    created_at = Column(DateTime, default=datetime.utcnow)

class ResourceOperation(Base):
    __tablename__ = "resource_operations"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    operation_uuid = Column(String(36), unique=True, nullable=False)
    resource_type = Column(String(30), nullable=False)
    resource_id = Column(BigInteger, nullable=True)
    action = Column(String(30), nullable=False)
    previous_state = Column(String(20), nullable=True)
    target_state = Column(String(20), nullable=True)
    status = Column(String(20), nullable=False, default="PENDING")
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

class ResourceLock(Base):
    __tablename__ = "resource_locks"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)

class ManagedLbService(Base):
    __tablename__ = "managed_lb_services"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(40), unique=True, nullable=False)
    layer = Column(String(2), nullable=False)
    specification = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="DRAFT")
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class ManagedNfsVolume(Base):
    __tablename__ = "managed_nfs_volumes"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(63), unique=True, nullable=False)
    file_path = Column(String(500), nullable=False)
    status = Column(String(20), nullable=False, default="READY")
    created_at = Column(DateTime, default=datetime.utcnow)

class ManagedNfsMount(Base):
    __tablename__ = "managed_nfs_mounts"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    volume_id = Column(BigInteger, ForeignKey("managed_nfs_volumes.id"), nullable=False)
    # schema.sql creates instances.id as BIGINT UNSIGNED on MySQL. Foreign-key
    # columns must match signedness exactly or MySQL rejects the constraint.
    instance_id = Column(
        BigInteger().with_variant(MySQLBigInteger(unsigned=True), "mysql"),
        ForeignKey("instances.id"),
        nullable=False,
    )
    target_path = Column(String(250), nullable=False)
    status = Column(String(20), nullable=False, default="PLANNED")
