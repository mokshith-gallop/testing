"""GCP credential resolution.

Resolution order:
  1. `BQ_ACCESS_TOKEN` — a short-lived OAuth access token supplied in the env file.
     On a Gallop workspace VM the platform writes the live connection details
     (native names) into that file, including this token; the VM has no service
     account and no interactive gcloud, so this is the only credential available.
  2. Application Default Credentials.
  3. The gcloud CLI's own access token, valid whenever `gcloud auth
     print-access-token` works — keeps the harness runnable in CI/dev without an
     interactive `gcloud auth application-default login`.
"""
from __future__ import annotations

import os
import subprocess

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def bigquery_credentials():
    import google.oauth2.credentials

    # 1. Pre-supplied access token (Gallop workspace VM). Self-contained so this
    # path never pulls in ADC's transport (requests) dependency. Do NOT attach
    # scopes: a pre-minted access token is already scoped server-side, and passing
    # scopes makes google-auth treat the bare token as refreshable — it then tries
    # to refresh on the first real request and fails (no refresh_token). The token
    # is short-lived; the platform rotates it in the env file in place.
    token = os.environ.get("BQ_ACCESS_TOKEN")
    if token:
        return google.oauth2.credentials.Credentials(token)

    # 2. ADC, then 3. gcloud token (standalone / sandbox use).
    import google.auth
    from google.auth.transport.requests import Request

    try:
        creds, _ = google.auth.default(scopes=_SCOPES)
        creds.refresh(Request())
        return creds
    except Exception:
        return _gcloud_token_credentials()


def _gcloud_token_credentials():
    import google.oauth2.credentials

    token = subprocess.check_output(
        ["gcloud", "auth", "print-access-token"], text=True
    ).strip()
    return google.oauth2.credentials.Credentials(token, scopes=_SCOPES)
