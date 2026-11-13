#!/bin/bash
# First call returns nothing (egress unknown), later calls return a valid /64.
C="${FAKE_PROBE_COUNTER:?explicit fake counter required}"
case "$C" in "${OUC_TEST_SANDBOX:?explicit test root required}"/*) ;; *) exit 91 ;; esac
n=$(cat "$C" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$C"
if [ "$n" -le 1 ]; then exit 0; fi
"$(dirname "$0")/fake_probe.sh"
