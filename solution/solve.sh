#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cp "$ROOT/solution/dispatch_audit/core.py" "$ROOT/environment/dispatch_audit/core.py"
cp "$ROOT/solution/dispatch_audit/__init__.py" "$ROOT/environment/dispatch_audit/__init__.py"
cp "$ROOT/solution/dispatch_audit/__main__.py" "$ROOT/environment/dispatch_audit/__main__.py"
