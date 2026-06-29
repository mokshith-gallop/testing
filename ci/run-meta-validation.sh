#!/usr/bin/env bash
# Layer-2 meta-validation (SPEC §9): seed the fixture migration into the live sandbox,
# run the ENTIRE Layer-1 golden suite against it, assert every test passes (incl. the
# negative twins, which must fail-as-designed), and emit the end-to-end sign-off report.
#
#   ci/run-meta-validation.sh [--no-teardown]
#
# Requires: a populated .env (see .env.example) + `gcloud auth application-default login`.
set -euo pipefail
cd "$(dirname "$0")/.."

NO_TEARDOWN="${1:-}"
PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY="python3"

echo "== 1/4  seed fixture migration (legacy + target + scratch) =="
"$PY" -m fixtures.load --all
PERF_SCALE="${PERF_SCALE:-100000}" "$PY" -m lib.synth tests/perf/synth.spec.yaml   # synth table for perf specs

echo "== 2/4  run Layer-1 golden + negative suite =="
"$PY" -m pytest -q

echo "== 3/4  end-to-end sign-off + coverage gate =="
mkdir -p .report
"$PY" -m lib.cli reconcile --quiet > .report/signoff.json 2> .report/signoff.txt || {
  cat .report/signoff.txt; echo "SIGN-OFF FAILED"; exit 1; }
cat .report/signoff.txt

if [ "$NO_TEARDOWN" != "--no-teardown" ]; then
  echo "== 4/4  teardown =="
  "$PY" -m fixtures.load --teardown || true
else
  echo "== 4/4  teardown skipped (--no-teardown) =="
fi
echo "META-VALIDATION GREEN"
