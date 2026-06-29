"""Migration Validation Spec (MVS) — load + validate.

The execution agent emits an MVS (YAML/JSON) of inputs/expected/criteria; the fixed
harness runs it (SPEC §4.1). This module validates the *envelope*; each pattern's
own slice is validated against its registered JSON Schema in lib/harness.py.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

# ${VAR} / ${VAR:-default} interpolation so committed specs stay env-portable
# (SPEC §8: env-var-only connectivity, no hardcoded dataset/host names).
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand_env(text: str) -> str:
    def repl(m: re.Match) -> str:
        val = os.environ.get(m.group(1))
        if val is None or val == "":
            val = m.group(2)
        if val is None:
            raise MVSError(f"undefined env var in MVS: ${{{m.group(1)}}}")
        return val
    return _VAR_RE.sub(repl, text)

# Envelope schema. `suites[].pattern` must name a registered pattern; the rest of
# each suite is validated per-pattern.
ENVELOPE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["suites"],
    "additionalProperties": True,
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "read_only": {"type": "boolean"},   # safe against a real env: mutating patterns blocked
        # Mode-2 build-and-verify: the harness applies these CUT artifacts to a clean
        # build dataset before the suites run (DESIGN-MODE2.md). Mutually exclusive with
        # read_only. SQL is applied verbatim; refs redirect via default-dataset.
        "migration": {
            "type": "object",
            "required": ["steps"],
            "properties": {
                "build_dataset": {"type": "string"},   # default dmt_build
                "isolate": {"type": "boolean"},          # unique per-run ds for parallel lanes
                "source_map": {"type": "array"},         # ground-truth doc (legacy -> target)
                "steps": {
                    "type": "array", "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["kind"],
                        "properties": {
                            "kind": {"enum": ["ddl", "load", "transform", "external"]},
                            "sql": {"type": "string"}, "sql_text": {"type": "string"},
                            "from": {"type": "string"}, "format": {"type": "string"},
                            "target": {"type": "string"}, "run": {"type": "object"},
                        },
                        "additionalProperties": True,
                    },
                },
            },
            "additionalProperties": True,
        },
        "connections": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "required": ["engine"],
                "properties": {
                    "engine": {"enum": ["bigquery", "impala", "hive"]},
                    "dataset": {"type": "string"},
                    "database": {"type": "string"},
                },
            },
        },
        "suites": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["pattern"],
                "properties": {
                    "pattern": {"type": "string"},
                    "id": {"type": "string"},
                    "story_id": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
    },
}

_envelope_validator = Draft202012Validator(ENVELOPE_SCHEMA)


class MVSError(ValueError):
    pass


def load_mvs(path: str | Path) -> dict:
    path = Path(path)
    # ${BUILD_DATASET} resolves to the default build dataset unless the caller
    # pre-set it (e.g. isolate mode injects a per-run name). Lets a committed spec
    # reference the build dataset its suites verify without hardcoding a name.
    os.environ.setdefault("BUILD_DATASET", "dmt_build")
    data = yaml.safe_load(expand_env(path.read_text()))
    if not isinstance(data, dict):
        raise MVSError(f"{path}: MVS must be a mapping at top level")
    data.setdefault("name", path.stem)
    validate_envelope(data, source=str(path))
    return data


def validate_envelope(data: dict, source: str = "<mvs>") -> None:
    errors = sorted(_envelope_validator.iter_errors(data), key=lambda e: e.path)
    if errors:
        msg = "; ".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors)
        raise MVSError(f"{source}: invalid MVS envelope: {msg}")
