"""The fixed harness: resolve connections, validate each suite against its pattern
schema, dispatch to the registered runner, aggregate a Report. The agent never
writes this code — it only emits the MVS the harness consumes (SPEC §4.1).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from . import config as cfg
from .engines import BigQueryEngine, Engine, HiveEngine, ImpalaEngine
from .mvs import MVSError, load_mvs
from .registry import PATTERNS, load_all_patterns
from .report import CheckResult, Report, Status, SuiteResult


class Context:
    """Resolved engines + per-spec connection metadata, handed to every runner."""

    def __init__(self, connections: dict[str, dict] | None = None):
        self.connections = connections or {}
        self._cache: dict[str, Engine] = {}

    # --- lazy engine accessors (raise only when actually used & unconfigured) ---
    # source/target engines are resolved from the MVS `connections` block, so a spec
    # can declare e.g. source: {engine: bigquery} for a BigQuery->BigQuery transform.
    # Defaults preserve the common legacy->target shape (Impala source, BQ target).
    @property
    def source_kind(self) -> str:
        return (self.connections.get("source") or {}).get("engine", "impala")

    @property
    def target_kind(self) -> str:
        return (self.connections.get("target") or {}).get("engine", "bigquery")

    @property
    def target(self) -> Engine:
        return self._engine(self.target_kind)

    @property
    def source(self) -> Engine:
        return self._engine(self.source_kind)

    @property
    def hive(self) -> HiveEngine:
        return self._engine("hive")

    @property
    def composer(self):
        """The orchestration engine (real Cloud Composer/Airflow), for DAG validation."""
        return self._engine("composer")

    def _engine(self, kind: str):
        # Configs are built lazily here (not in __init__), so a spec that only uses
        # one engine never asserts the other's env vars. A missing var raises
        # ConfigError -> the suite fails (no silent skip, no default).
        if kind in self._cache:
            return self._cache[kind]
        if kind == "bigquery":
            eng = BigQueryEngine(cfg.BigQueryConfig.from_env())
        elif kind == "impala":
            eng = ImpalaEngine(cfg.ImpalaConfig.from_env())
        elif kind == "hive":
            eng = HiveEngine(cfg.ImpalaConfig.from_env())
        elif kind == "composer":
            from .engines import ComposerEngine
            eng = ComposerEngine(cfg.ComposerConfig.from_env())
        else:
            raise ValueError(f"unknown engine kind: {kind}")
        self._cache[kind] = eng
        return eng


def run_spec(path: str | Path) -> Report:
    """Load + run an MVS file end to end."""
    load_all_patterns()
    data = load_mvs(path)
    return run_mvs(data)


def validate_spec(path: str | Path) -> list[str]:
    """Schema-validate a spec WITHOUT running it (no DB, no connections): the MVS envelope plus
    each suite against its pattern's schema — the exact checks the harness runs before any DB call.
    Returns a list of human-readable problems (empty list = valid). Lets the agent lint a spec it
    just authored and fix it before committing, instead of discovering the error only at run time."""
    load_all_patterns()
    try:
        data = load_mvs(path)        # YAML parse + envelope schema
    except Exception as e:           # noqa: BLE001 — a linter reports, never crashes
        return [f"{type(e).__name__}: {e}"]
    problems: list[str] = []
    for i, suite in enumerate(data.get("suites", [])):
        pattern = suite.get("pattern", "")
        sid = suite.get("id") or f"{pattern}#{i}"
        spec = PATTERNS.get(pattern)
        if spec is None:
            problems.append(f"{sid}: unknown pattern '{pattern}' (registered: {sorted(PATTERNS)})")
            continue
        for e in sorted(Draft202012Validator(spec.schema).iter_errors(suite), key=lambda e: list(e.path)):
            loc = "/".join(map(str, e.path)) or "<suite>"
            problems.append(f"{sid}/{loc}: {e.message}")
    return problems


def run_mvs(data: dict, base_dir: str = ".") -> Report:
    load_all_patterns()
    ctx = Context(connections=data.get("connections"))
    # read_only: the spec is safe to run against a REAL (production) environment — it
    # only validates existing state, never seeds/mutates. The harness BLOCKS any
    # mutating pattern under this flag (fail-fast guard), so you can't accidentally
    # point a load/merge/export spec at prod.
    read_only = bool(data.get("read_only", False))
    migration = data.get("migration")
    report = Report(spec_name=data.get("name", "<mvs>"))

    # Mode-2 build-and-verify: apply the CUT's artifacts to a clean build dataset, then
    # let the suites verify. read_only + migration is a contradiction (you can't build
    # against a target you've declared off-limits). See DESIGN-MODE2.md.
    if migration and read_only:
        return _build_error(report, "spec sets both read_only and migration: build-and-verify "
                            "needs a writable build dataset; read_only forbids mutation")
    build_ds = None
    if migration:
        build_ds = _build(migration, ctx, report, base_dir)
        if build_ds is None:        # build failed -> report already carries the ERROR suite
            return report

    # Optionally stand up the legacy source from its real DDL (sandbox-only, guarded by
    # DMT_SOURCE_SETUP), so the source cross-check has a live source to read. No-op against a
    # real legacy (no opt-in). Always torn down in the finally.
    source_created = []
    try:
        # inside the try so a setup_source failure still hits the finally and tears down build_ds
        if data.get("source_setup"):
            from . import build
            source_created = build.setup_source(ctx, data["source_setup"], base_dir)
        for i, suite in enumerate(data.get("suites", [])):
            report.suites.append(_run_suite(suite, ctx, index=i, read_only=read_only))
    finally:
        from . import build
        if build_ds and migration.get("isolate"):
            build.teardown(ctx.target, build_ds)
        if source_created:
            build.teardown_source(ctx, source_created)
    return report


def _build_error(report: Report, message: str) -> Report:
    sr = SuiteResult(pattern="migration_build", suite_id="build")
    sr.add(CheckResult(pattern="migration_build", target="build", status=Status.ERROR, message=message))
    report.suites.append(sr)
    return report


def _build(migration: dict, ctx: Context, report: Report, base_dir: str) -> str | None:
    """Provision a clean build dataset and apply the CUT's steps verbatim. Returns the
    build dataset name, or None if the build failed (report gets an ERROR suite)."""
    from . import build

    if ctx.target_kind != "bigquery":
        _build_error(report, f"build-and-verify target must be bigquery, got '{ctx.target_kind}'")
        return None
    bq = ctx.target
    name = migration.get("build_dataset") or build.BUILD_DS_DEFAULT
    try:
        build.provision_build_dataset(bq, name)
        for step in migration.get("steps", []):
            build.apply_step(bq, name, step, base_dir)
        return name
    except Exception as e:  # noqa: BLE001 — any build failure -> ERROR (don't judge a half-built target)
        if migration.get("isolate"):
            try:
                build.teardown(bq, name)
            except Exception:  # noqa: BLE001
                pass
        _build_error(report, f"{type(e).__name__}: {e}")
        return None


def _run_suite(suite: dict, ctx: Context, index: int, read_only: bool = False) -> SuiteResult:
    pattern = suite.get("pattern", "")
    suite_id = suite.get("id") or f"{pattern}#{index}"
    spec = PATTERNS.get(pattern)
    if spec is None:
        sr = SuiteResult(pattern=pattern, suite_id=suite_id)
        sr.add(CheckResult(pattern=pattern, target=suite_id, status=Status.ERROR,
                           message=f"unknown pattern '{pattern}' (registered: {sorted(PATTERNS)})"))
        return sr

    if read_only and spec.mutates:
        sr = SuiteResult(pattern=pattern, suite_id=suite_id)
        sr.add(CheckResult(pattern=pattern, target=suite_id, status=Status.ERROR,
                           message=f"pattern '{pattern}' mutates state — blocked in read_only mode "
                                   f"(it seeds/loads/exports; not safe against a real environment)"))
        return sr

    # Per-pattern schema validation — loud failure on bad agent input.
    errs = sorted(Draft202012Validator(spec.schema).iter_errors(suite), key=lambda e: list(e.path))
    if errs:
        sr = SuiteResult(pattern=pattern, suite_id=suite_id)
        msg = "; ".join(f"{'/'.join(map(str, e.path)) or '<suite>'}: {e.message}" for e in errs[:6])
        sr.add(CheckResult(pattern=pattern, target=suite_id, status=Status.ERROR,
                           message=f"invalid suite spec: {msg}"))
        return sr

    try:
        return spec.runner(suite, ctx)
    except Exception as e:  # noqa: BLE001 — runner crash (incl. ConfigError) -> ERROR
        sr = SuiteResult(pattern=pattern, suite_id=suite_id)
        sr.add(CheckResult(pattern=pattern, target=suite_id, status=Status.ERROR,
                           message=f"{type(e).__name__}: {e}"))
        return sr


def require(ctx: Context, *kinds: str) -> None:
    """Build the engines a runner needs up front, so a missing env var fails fast
    (ConfigError -> ERROR) before any partial work runs."""
    for k in kinds:
        ctx._engine(k)
