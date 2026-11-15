#!/usr/bin/env bash
# Deterministic stand-in for the opencode binary. The trailing bytes exist so a
# test can prove that nothing was appended, prefixed or re-encoded.
printf 'fake-opencode arg-count=%d\n' "$#"
printf 'ANSI: \033[1;31mred\033[0m\n'
printf 'no-trailing-newline-marker'
exit 0
