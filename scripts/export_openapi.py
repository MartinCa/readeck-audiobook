"""Write the API's OpenAPI spec to frontend/openapi.json.

The frontend generates its types from that file (`pnpm generate:api-types`),
and tests/test_openapi.py fails when it drifts from the code. Run from the
repository root after changing an endpoint or schema:

    python scripts/export_openapi.py
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "frontend" / "openapi.json"


def render() -> str:
    # The app refuses to import without these; the values never reach the spec.
    os.environ.setdefault("READECK_BASE_URL", "http://readeck.invalid")
    os.environ.setdefault("READECK_API_TOKEN", "unused")
    sys.path.insert(0, str(ROOT))
    from app.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    SPEC_PATH.write_text(render())
    print(f"Wrote {SPEC_PATH.relative_to(ROOT)}")
