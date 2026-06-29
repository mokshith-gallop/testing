"""Deliberately-broken DAG — an unresolvable import. The orchestration check must
surface this via `dags list-import-errors` (the authoritative "0 import errors" AC)."""
from datetime import datetime

from airflow import DAG
import nonexistent_module_xyz  # noqa: F401 — intentional import error

with DAG("broken_dag", start_date=datetime(2026, 1, 1), schedule_interval=None):
    pass
