"""Environment-driven config — fail-fast, no silent fallbacks.

No hardcoded hosts/credentials/dataset names anywhere in the library: connectivity
comes entirely from env. Every var is REQUIRED — a missing one raises ConfigError
with a clear message (it does not silently default, and tests do not skip). To run
a subset, select the tests you want; their engines assert only the vars they use.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Standalone/sandbox: load a local .env if present (real env vars win over it).
load_dotenv(override=False)
# Gallop workspace VM: the platform writes live creds (native names, incl. the
# rotating BigQuery token) to this fixed path, OUTSIDE the repo (so they're never
# committed and never collide with the app's own .env). override=True so a freshly
# rotated value always beats a stale one left in the environment; a no-op when the
# file is absent (i.e. standalone use).
load_dotenv("/workspace/.gallop/db.env", override=True)


class ConfigError(RuntimeError):
    pass


def require_env(name: str) -> str:
    """Return the env var, or raise ConfigError if unset/empty (no fallback).
    Use for every test-identity var (project, host, dataset, db)."""
    v = os.environ.get(name)
    if v is None or v == "":
        raise ConfigError(f"required env var {name} is not set")
    return v


@dataclass(frozen=True)
class BigQueryConfig:
    project: str
    location: str

    @classmethod
    def from_env(cls) -> "BigQueryConfig":
        return cls(project=require_env("GCP_PROJECT"), location=require_env("BQ_LOCATION"))


@dataclass(frozen=True)
class ImpalaConfig:
    host: str            # shared by Impala (:21050 reads) and Hive (:10000 writes) — bundled VM
    impala_port: int
    hive_port: int
    auth: str  # NONE | KERBEROS | LDAP
    # HS2 connection bootstrap schema. Always "default" — every query is fully
    # qualified (db.table), so the connection's default DB is never otherwise used.
    database: str = "default"

    @property
    def hive_host(self) -> str:
        return self.host

    @classmethod
    def from_env(cls) -> "ImpalaConfig":
        return cls(
            host=require_env("IMPALA_HOST"),
            impala_port=int(require_env("IMPALA_PORT")),
            hive_port=int(require_env("HIVE_PORT")),
            auth=require_env("IMPALA_AUTH"),
        )


@dataclass(frozen=True)
class ComposerConfig:
    """A real Cloud Composer (Airflow) environment — the migration's orchestrator."""
    project: str
    environment: str
    location: str

    @classmethod
    def from_env(cls) -> "ComposerConfig":
        return cls(project=require_env("GCP_PROJECT"),
                   environment=require_env("COMPOSER_ENV"),
                   location=require_env("COMPOSER_REGION"))
