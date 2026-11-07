#!/usr/bin/env bash
# Regression checks for schema.sql + Alembic compatibility on MySQL.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENTITIES="${ROOT}/backend/app/models/entities.py"
DNS_MIGRATION="${ROOT}/backend/alembic/versions/0006_dns_hostname.py"
ADDONS_MIGRATION="${ROOT}/backend/alembic/versions/0007_managed_addons.py"

python3 -m py_compile "${ENTITIES}" "${DNS_MIGRATION}" "${ADDONS_MIGRATION}"
grep -Fq 'MySQLBigInteger(unsigned=True)' "${ENTITIES}"
grep -Fq 'if "dns_hostname" not in columns' "${DNS_MIGRATION}"
grep -Fq 'if "ix_instances_dns_hostname" not in indexes' "${DNS_MIGRATION}"
grep -Fq 'if "managed_nfs_mounts" not in tables' "${ADDONS_MIGRATION}"
grep -Fq 'MySQLBigInteger(unsigned=True)' "${ADDONS_MIGRATION}"

printf '[PASS] MySQL migration compatibility regression checks\n'
