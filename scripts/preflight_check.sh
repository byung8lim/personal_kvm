#!/usr/bin/env bash
# Read-only host compatibility gate. Override paths for isolated tests only.
set -euo pipefail

OS_RELEASE_FILE="${PKVM_OS_RELEASE_FILE:-/etc/os-release}"
CPUINFO_FILE="${PKVM_CPUINFO_FILE:-/proc/cpuinfo}"
[[ -r "$OS_RELEASE_FILE" ]] || { echo "[FAIL] os-release unavailable: $OS_RELEASE_FILE" >&2; exit 1; }
[[ -r "$CPUINFO_FILE" ]] || { echo "[FAIL] cpuinfo unavailable: $CPUINFO_FILE" >&2; exit 1; }

# Do not execute untrusted os-release text: parse only the expected keys.
field() {
    local line value
    line="$(grep -m1 -E "^$1=" "$OS_RELEASE_FILE" || true)"
    value="${line#*=}"
    value="${value#\"}"; value="${value%\"}"
    printf '%s' "$value"
}
id="$(field ID)"
version="$(field VERSION_ID)"
major="${version%%.*}"
[[ "$id" == rocky && ( "$major" == 9 || "$major" == 10 ) ]] || {
    echo "[FAIL] Rocky Linux 9.x/10.x required (ID=$id VERSION_ID=$version)" >&2; exit 1;
}

flags="$(awk '/^(flags|Features)[[:space:]]*:/ {sub(/^[^:]*:[[:space:]]*/, ""); print; exit}' "$CPUINFO_FILE")"
[[ -n "$flags" ]] || { echo '[FAIL] CPU flags not found; cannot verify KVM prerequisites' >&2; exit 1; }
has_flag() { [[ " $flags " == *" $1 "* ]]; }
failures=0
if ! has_flag vmx && ! has_flag svm; then
    echo '[FAIL] VT-x/AMD-V (vmx/svm) not exposed. Enable virtualization in firmware or nested KVM.' >&2
    failures=$((failures + 1))
fi
if [[ "$major" == 10 && "$(uname -m)" == x86_64 ]]; then
    # x86-64-v3 also requires x86-64-v2 baseline features; AVX requires OSXSAVE.
    for required in avx avx2 bmi1 bmi2 fma movbe f16c xsave osxsave lzcnt sse3 ssse3 sse4_1 sse4_2 popcnt cx16 lahf_lm; do
        # Linux reports SSE3 as pni, and BMI1 as bmi1 or bmi.
        match="$required"
        [[ "$required" == sse3 ]] && match=pni
        if [[ "$required" == bmi1 ]] && has_flag bmi; then continue; fi
        if [[ "$required" == lzcnt ]] && has_flag abm; then continue; fi
        if ! has_flag "$match"; then
            echo "[FAIL] Rocky 10 x86-64-v3 prerequisite missing: $required" >&2
            failures=$((failures + 1))
        fi
    done
fi
if [[ "$major" == 10 && "$(uname -m)" != x86_64 ]]; then
    echo '[FAIL] This KVM project targets x86_64; Rocky 10 host architecture differs.' >&2
    failures=$((failures + 1))
fi
if (( failures > 0 )); then
    echo "[FAIL] $failures CPU capability checks failed; no system changes made." >&2
    exit 1
fi
echo "[PASS] Rocky $version / $(uname -m) CPU compatibility preflight passed"
