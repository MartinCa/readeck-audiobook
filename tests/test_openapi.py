"""The frontend's API types are generated from frontend/openapi.json."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "export_openapi.py"


def test_checked_in_spec_matches_the_code():
    spec = importlib.util.spec_from_file_location("export_openapi", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.SPEC_PATH.read_text() == module.render(), (
        "frontend/openapi.json is stale: run `python scripts/export_openapi.py`, "
        "then `pnpm generate:api-types` in frontend/"
    )
