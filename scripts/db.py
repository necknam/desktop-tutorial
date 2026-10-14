"""DB CLI; resolves paths from its own location."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.db.deployment import main

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(1)
