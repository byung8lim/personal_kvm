CREATE DATABASE IF NOT EXISTS personal_kvm CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE personal_kvm;

CREATE TABLE IF NOT EXISTS admins (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    must_change_password TINYINT(1) NOT NULL DEFAULT 1,
    last_login_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS os_images (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    linux_type ENUM('ROCKY', 'UBUNTU') NOT NULL,
    version VARCHAR(20) NOT NULL,
    image_format VARCHAR(15) NOT NULL DEFAULT 'qcow2',
    size_mb INT UNSIGNED NOT NULL DEFAULT 0,
    source_url VARCHAR(500) NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    checksum_sha256 VARCHAR(64) NULL,
    default_user VARCHAR(30) NOT NULL DEFAULT 'rocky',
    root_partition VARCHAR(30) NOT NULL DEFAULT '/dev/sda4',
    released_at DATETIME NULL,
    registered_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status ENUM('MISSING', 'DOWNLOADING', 'READY', 'DELETING', 'DELETED', 'ERROR') NOT NULL DEFAULT 'MISSING',
    INDEX idx_os_type_ver (linux_type, version),
    INDEX idx_os_status (status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS storage_pools (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    pool_name VARCHAR(50) NOT NULL UNIQUE,
    mount_path VARCHAR(255) NOT NULL,
    pool_type ENUM('ROOT', 'EXTRA', 'IMAGE') NOT NULL,
    total_size_gb DECIMAL(10,2) NOT NULL DEFAULT 500.00,
    allocated_size_gb DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    available_size_gb DECIMAL(10,2) NOT NULL DEFAULT 500.00,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS virtual_networks (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    net_name VARCHAR(50) NOT NULL UNIQUE,
    net_type ENUM('BRIDGED', 'NAT', 'INTERNAL', 'MANAGEMENT') NOT NULL,
    bridge_name VARCHAR(20) NOT NULL UNIQUE,
    cidr VARCHAR(25) NOT NULL,
    gateway_ip VARCHAR(15) NULL,
    dns_server VARCHAR(15) NULL DEFAULT '8.8.8.8',
    ip_start VARCHAR(15) NOT NULL DEFAULT '192.168.0.101',
    ip_end VARCHAR(15) NOT NULL DEFAULT '192.168.0.200',
    total_ips INT UNSIGNED NOT NULL DEFAULT 100,
    assigned_ips INT UNSIGNED NOT NULL DEFAULT 0,
    is_active TINYINT(1) NOT NULL DEFAULT 1
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS instances (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    instance_uuid CHAR(36) NOT NULL UNIQUE,
    name VARCHAR(64) NOT NULL UNIQUE,
    dns_hostname VARCHAR(64) NOT NULL,
    os_image_id BIGINT UNSIGNED NOT NULL,
    vcores TINYINT UNSIGNED NOT NULL DEFAULT 2,
    memory_gb TINYINT UNSIGNED NOT NULL DEFAULT 4,
    root_disk_gb INT UNSIGNED NOT NULL DEFAULT 20,
    extra_disk_gb INT UNSIGNED NOT NULL DEFAULT 0,
    public_ip VARCHAR(15) NULL,
    private_ip VARCHAR(15) NULL,
    mgmt_ip VARCHAR(15) NULL,
    state ENUM('CREATING', 'STARTING', 'RUNNING', 'STOPPING', 'STOPPED', 'RESIZING', 'DELETING', 'DELETED', 'ERROR') NOT NULL DEFAULT 'CREATING',
    last_desired_state ENUM('RUNNING', 'STOPPED') NOT NULL DEFAULT 'RUNNING',
    error_message TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at DATETIME NULL,
    CONSTRAINT fk_inst_image FOREIGN KEY (os_image_id) REFERENCES os_images(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS virtual_disks (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    disk_uuid CHAR(36) NOT NULL UNIQUE,
    disk_name VARCHAR(100) NOT NULL,
    pool_id BIGINT UNSIGNED NOT NULL,
    disk_role ENUM('ROOT', 'EXTRA') NOT NULL,
    size_gb INT UNSIGNED NOT NULL DEFAULT 20,
    file_path VARCHAR(500) NOT NULL,
    is_assigned TINYINT(1) NOT NULL DEFAULT 0,
    instance_id BIGINT UNSIGNED NULL,
    target_dev VARCHAR(10) NULL,
    status ENUM('CREATING', 'AVAILABLE', 'IN_USE', 'DELETING', 'DELETED', 'ERROR') NOT NULL DEFAULT 'AVAILABLE',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at DATETIME NULL,
    CONSTRAINT fk_disk_pool FOREIGN KEY (pool_id) REFERENCES storage_pools(id),
    CONSTRAINT fk_disk_inst FOREIGN KEY (instance_id) REFERENCES instances(id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS ip_pools (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    network_id BIGINT UNSIGNED NOT NULL,
    ip_address VARCHAR(15) NOT NULL UNIQUE,
    status ENUM('AVAILABLE', 'ASSIGNED', 'RESERVED') NOT NULL DEFAULT 'AVAILABLE',
    instance_id BIGINT UNSIGNED NULL,
    assigned_at DATETIME NULL,
    INDEX idx_ip_net_status (network_id, status),
    CONSTRAINT fk_ip_net FOREIGN KEY (network_id) REFERENCES virtual_networks(id) ON DELETE CASCADE,
    CONSTRAINT fk_ip_inst FOREIGN KEY (instance_id) REFERENCES instances(id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS instance_nics (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    instance_id BIGINT UNSIGNED NOT NULL,
    network_id BIGINT UNSIGNED NOT NULL,
    ip_pool_id BIGINT UNSIGNED NOT NULL UNIQUE,
    iface_name VARCHAR(10) NOT NULL,
    mac_address CHAR(17) NOT NULL UNIQUE,
    is_default_gw TINYINT(1) NOT NULL DEFAULT 0,
    CONSTRAINT fk_nic_inst FOREIGN KEY (instance_id) REFERENCES instances(id) ON DELETE CASCADE,
    CONSTRAINT fk_nic_net FOREIGN KEY (network_id) REFERENCES virtual_networks(id),
    CONSTRAINT fk_nic_ip FOREIGN KEY (ip_pool_id) REFERENCES ip_pools(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS resource_operations (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    operation_uuid CHAR(36) NOT NULL UNIQUE,
    resource_type VARCHAR(30) NOT NULL,
    resource_id BIGINT UNSIGNED NULL,
    action VARCHAR(30) NOT NULL,
    previous_state VARCHAR(20) NULL,
    target_state VARCHAR(20) NULL,
    status ENUM('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED') NOT NULL DEFAULT 'PENDING',
    error_message TEXT NULL,
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME NULL,
    INDEX idx_operation_resource (resource_type, resource_id),
    INDEX idx_operation_status (status, started_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS resource_locks (
    id INT PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE
) ENGINE=InnoDB;
INSERT IGNORE INTO resource_locks (id, name) VALUES (1, 'host-allocation');
