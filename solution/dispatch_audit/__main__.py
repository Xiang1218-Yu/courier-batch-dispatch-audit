from __future__ import annotations

# This file is the candidate process entrypoint. It also supports direct script
# execution so the verifier can invoke this concrete path without importing a
# candidate-controlled module through the current working directory.
if __package__:
    from .core import main
else:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dispatch_audit.core import main

if __name__ == "__main__":
    raise SystemExit(main())
