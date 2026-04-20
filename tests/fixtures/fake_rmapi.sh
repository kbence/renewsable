#!/bin/bash
# fake_rmapi.sh — test double for rmapi.
#
# Supports the two subcommands renewsable invokes:
#
#   rmapi mkdir <folder>
#   rmapi put --force <pdf> <folder>/
#
# The fake records every invocation (one argv-joined line per call) into
# the file named by $FAKE_RMAPI_LOG, if set. The exit code is read from
# $FAKE_RMAPI_EXIT (default 0) and an optional stderr line is printed
# from $FAKE_RMAPI_STDERR before exiting.
#
# This is a *single-invocation* double: it does not implement a scripted
# exit-code queue. Multi-call retry sequences are exercised via the
# Python-level ``_ScriptedRun`` in tests/test_uploader.py which replaces
# subprocess.run directly and is far more precise.

if [[ -n "$FAKE_RMAPI_LOG" ]]; then
  printf '%s\n' "$*" >> "$FAKE_RMAPI_LOG"
fi

if [[ -n "$FAKE_RMAPI_STDERR" ]]; then
  printf '%s\n' "$FAKE_RMAPI_STDERR" >&2
fi

exit "${FAKE_RMAPI_EXIT:-0}"
