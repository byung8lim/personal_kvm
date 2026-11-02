#!/bin/bash
set -euo pipefail
TLS_DIR=/etc/personal-kvm/tls
CERT="${TLS_DIR}/server.crt"; KEY="${TLS_DIR}/server.key"; RENEW_DAYS="${PKVM_TLS_RENEW_DAYS:-30}"
if [ -s "${CERT}" ] && openssl x509 -checkend "$((RENEW_DAYS * 86400))" -noout -in "${CERT}"; then exit 0; fi
if [ -n "${PKVM_TLS_RENEW_COMMAND:-}" ]; then /bin/bash -c "${PKVM_TLS_RENEW_COMMAND}"; else
  host_name="$(hostname -f 2>/dev/null || hostname)"; tmp_dir="$(mktemp -d "${TLS_DIR}/renew.XXXXXX")"; trap 'rm -rf -- "${tmp_dir}"' EXIT
  openssl req -x509 -newkey rsa:3072 -sha256 -days 365 -nodes -subj "/CN=${host_name}" -addext "subjectAltName=DNS:${host_name},DNS:$(hostname)" -keyout "${tmp_dir}/server.key" -out "${tmp_dir}/server.crt"
  install -o root -g pkvm -m 0640 "${tmp_dir}/server.key" "${KEY}"; install -o root -g pkvm -m 0644 "${tmp_dir}/server.crt" "${CERT}"
fi
nginx -t; systemctl reload nginx
