"""JSON Schema validation for tracer documents."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .spec import validate_tracer


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    packaged = resources.files("tracer_spec").joinpath("tracer.schema.json")
    if packaged.is_file():
        doc: dict[str, Any] = json.loads(packaged.read_text(encoding="utf-8"))
        return doc
    # Editable install inside the monorepo.
    repo = Path(__file__).resolve().parents[2] / "schema" / "tracer.schema.json"
    repo_doc: dict[str, Any] = json.loads(repo.read_text(encoding="utf-8"))
    return repo_doc


def validate_schema(doc: Any) -> list[str]:
    validator = Draft202012Validator(load_schema())
    return [
        f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    ]


def validate(doc: Any) -> list[str]:
    """Full validation: schema first, then cross-field rules."""
    errors = validate_schema(doc)
    if errors:
        return errors
    return validate_tracer(doc)
