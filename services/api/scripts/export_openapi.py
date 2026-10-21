"""Write the API's OpenAPI schema (deterministic JSON) for the TypeScript client generator.

Usage: uv run python services/api/scripts/export_openapi.py packages/api-client/openapi.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tracer_api.auth import TokenVerifier
from tracer_api.main import create_app
from tracer_core.config import Settings
from tracer_core.storage import LocalStorage


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "packages/api-client/openapi.json")
    app = create_app(
        Settings(supabase_service_role_key="unused"),
        storage=LocalStorage(Path(".")),
        verifier=TokenVerifier(jwks_url=None, audience="authenticated"),
        manage_pool=False,
    )
    schema = app.openapi()
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
