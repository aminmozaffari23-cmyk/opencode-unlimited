#!/bin/bash
# First call returns nothing (egress unknown), later calls return a valid /64.
C="${FAKE_PROBE_COUNTER:-/tmp/fake-probe-count}"
n=$(cat "$C" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$C"
if [ "$n" -le 1 ]; then exit 0; fi
"$(dirname "$0")/fake_probe.sh"
