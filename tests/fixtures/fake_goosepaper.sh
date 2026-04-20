#!/bin/bash
# fake_goosepaper.sh — test double for goosepaper.
#
# Accepts `-c <config>` and `-o <output>` (and any other flags such as
# `--noupload`); writes a small but valid PDF magic-byte stub to the
# output path and exits 0. The generated file is not a real PDF, but it
# starts with `%PDF-` which is what renewsable.builder.Builder checks.
output=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o)
      output="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

if [[ -z "$output" ]]; then
  echo "fake_goosepaper: -o <output> is required" >&2
  exit 2
fi

printf '%%PDF-1.4\n%%fake stub for tests\n%%%%EOF\n' > "$output"
exit 0
