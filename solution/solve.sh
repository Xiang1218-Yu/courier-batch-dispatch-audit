#!/bin/sh
set -eu
SOLUTION_DIR=${SOLUTION_DIR:-/solution}
PROGRAM_DIR=${PROGRAM_DIR:-/app}
cp "$SOLUTION_DIR/dispatch_audit/core.py" "$PROGRAM_DIR/dispatch_audit/core.py"
cp "$SOLUTION_DIR/dispatch_audit/__init__.py" "$PROGRAM_DIR/dispatch_audit/__init__.py"
cp "$SOLUTION_DIR/dispatch_audit/__main__.py" "$PROGRAM_DIR/dispatch_audit/__main__.py"
