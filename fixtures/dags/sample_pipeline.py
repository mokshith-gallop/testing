"""Sample migration DAG for validating the orchestration pattern (Airflow 2.x /
Composer 2). Uses only core operators so it parses anywhere; structure mirrors a
real staging->ods->dm build: 3 tasks, retries, daily schedule, failure callback."""
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.empty import EmptyOperator


def _on_failure(context):  # noqa: ARG001 — referenced so the callback is present
    pass


default_args = {"retries": 3, "retry_delay": timedelta(minutes=5),
                "on_failure_callback": _on_failure}

with DAG(
    dag_id="nbcs_sample_pipeline",
    schedule_interval="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["dmtemplate", "sample"],
) as dag:
    ingest = EmptyOperator(task_id="ingest")
    transform = EmptyOperator(task_id="transform")
    publish = EmptyOperator(task_id="publish")
    ingest >> transform >> publish
