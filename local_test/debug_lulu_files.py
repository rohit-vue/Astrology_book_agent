#!/usr/bin/env python3
"""
Local Lulu Print API debugger (aligned with openapi_public (1).yml).

Uses the same OAuth client-credentials flow as production Lambdas. Set credentials
in local_test/.env (or repo-root .env):

  LULU_CLIENT_KEY=...
  LULU_CLIENT_SECRET=...

Aliases from Secrets Manager naming also work: LuluApiClientKey / LuluApiClientSecret.

Optional:
  LULU_API_BASE=https://api.lulu.com          # or https://api.sandbox.lulu.com
  LULU_POD_PACKAGE_ID=0550X0850BWSTDLW060UC444MNG   # matches notify_lulu default

Commands (OpenAPI operation references):
  cover-dimensions   POST /cover-dimensions/  (Cover-Dimensions_create)
  validate-cover     POST /validate-cover/ + GET /validate-cover/{id}/ (Validate-Cover_*)
  validate-interior  POST /validate-interior/ + GET /validate-interior/{id}/ (Validate-Interior_*)
  print-job          GET /print-jobs/{id}/ (Print-Jobs_read) — surfaces line item errors

Example (cover size mismatch — width must fall in Lulu's inch band for your POD + page count):
  python debug_lulu_files.py cover-dimensions --pages 140
  python debug_lulu_files.py validate-cover --url \"https://...cover.pdf\" --pages 140
  python debug_lulu_files.py print-job 2860285
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")
load_dotenv(HERE.parent / ".env")

DEFAULT_POD = os.environ.get("LULU_POD_PACKAGE_ID", "0550X0850.BW.STD.LW.060UC444.MNG")
DEFAULT_API_BASE = os.environ.get("LULU_API_BASE", "https://api.lulu.com").rstrip("/")


def _client_creds() -> tuple[str, str]:
    key = os.environ.get("LULU_CLIENT_KEY") or os.environ.get("LuluApiClientKey")
    secret = os.environ.get("LULU_CLIENT_SECRET") or os.environ.get("LuluApiClientSecret")
    if not key or not secret:
        print(
            "Missing LULU_CLIENT_KEY / LULU_CLIENT_SECRET "
            "(or LuluApiClientKey / LuluApiClientSecret) in environment.",
            file=sys.stderr,
        )
        sys.exit(1)
    return key, secret


def get_lulu_token(api_base: str, client_key: str, client_secret: str) -> str:
    auth_url = f"{api_base}/auth/realms/glasstree/protocol/openid-connect/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    payload = {"grant_type": "client_credentials"}
    r = requests.post(auth_url, headers=headers, auth=(client_key, client_secret), data=payload, timeout=120)
    r.raise_for_status()
    return r.json()["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }


def cmd_cover_dimensions(api_base: str, token: str, pod_package_id: str, pages: int, unit: str) -> None:
    """POST /cover-dimensions/ — expected cover size for POD package + interior page count."""
    url = f"{api_base}/cover-dimensions/"
    body = {"pod_package_id": pod_package_id, "interior_page_count": pages, "unit": unit}
    r = requests.post(url, headers=_auth_headers(token), json=body, timeout=120)
    print(f"POST {url}\n{json.dumps(body, indent=2)}\n---\nHTTP {r.status_code}")
    print(r.text)
    r.raise_for_status()
    data = r.json()
    print("\nInterpretation: Lulu expects your *cover* PDF (full spread) to match these dimensions "
          f"({data.get('unit')}). Compare to your file's media box in Acrobat or a PDF library.")


def _poll_validate_cover(api_base: str, token: str, validation_id: int, interval: float, max_wait: float) -> dict[str, Any]:
    url = f"{api_base}/validate-cover/{validation_id}/"
    terminal = {"NORMALIZED", "ERROR"}
    deadline = time.monotonic() + max_wait
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        r = requests.get(url, headers=_auth_headers(token), timeout=120)
        r.raise_for_status()
        last = r.json()
        status = last.get("status") or ""
        print(f"  validate-cover id={validation_id} status={status!r} errors={last.get('errors')!r}")
        if status in terminal:
            return last
        time.sleep(interval)
    print("Timed out waiting for cover validation.", file=sys.stderr)
    return last


def _poll_validate_interior(api_base: str, token: str, validation_id: int, interval: float, max_wait: float) -> dict[str, Any]:
    url = f"{api_base}/validate-interior/{validation_id}/"
    terminal = {"VALIDATED", "ERROR"}
    deadline = time.monotonic() + max_wait
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        r = requests.get(url, headers=_auth_headers(token), timeout=120)
        r.raise_for_status()
        last = r.json()
        status = last.get("status") or ""
        print(
            f"  validate-interior id={validation_id} status={status!r} "
            f"errors={last.get('errors')!r} page_count={last.get('page_count')!r}"
        )
        if status in terminal:
            return last
        time.sleep(interval)
    print("Timed out waiting for interior validation.", file=sys.stderr)
    return last


def cmd_validate_cover(
    api_base: str,
    token: str,
    source_url: str,
    pod_package_id: str,
    pages: int,
    interval: float,
    max_wait: float,
) -> None:
    url = f"{api_base}/validate-cover/"
    body = {"source_url": source_url, "pod_package_id": pod_package_id, "interior_page_count": pages}
    r = requests.post(url, headers=_auth_headers(token), json=body, timeout=120)
    print(f"POST {url}\n{json.dumps(body, indent=2)}\n---\nHTTP {r.status_code}\n{r.text}")
    r.raise_for_status()
    vid = int(r.json()["id"])
    print("Polling GET /validate-cover/{id}/ until NORMALIZED or ERROR ...")
    result = _poll_validate_cover(api_base, token, vid, interval, max_wait)
    print("\nFinal:", json.dumps(result, indent=2))


def cmd_validate_interior(
    api_base: str,
    token: str,
    source_url: str,
    pod_package_id: str | None,
    interval: float,
    max_wait: float,
) -> None:
    url = f"{api_base}/validate-interior/"
    body: dict[str, Any] = {"source_url": source_url}
    if pod_package_id:
        body["pod_package_id"] = pod_package_id
    r = requests.post(url, headers=_auth_headers(token), json=body, timeout=120)
    print(f"POST {url}\n{json.dumps(body, indent=2)}\n---\nHTTP {r.status_code}\n{r.text}")
    r.raise_for_status()
    vid = int(r.json()["id"])
    print("Polling GET /validate-interior/{id}/ until VALIDATED or ERROR ...")
    result = _poll_validate_interior(api_base, token, vid, interval, max_wait)
    print("\nFinal:", json.dumps(result, indent=2))


def _extract_print_job_messages(job: dict[str, Any]) -> None:
    """Surface line_items[].status.messages (incl. printable_normalization) per OpenAPI."""
    items = job.get("line_items") or job.get("items") or []
    for li in items:
        lid = li.get("id")
        st = li.get("status") or {}
        name = st.get("name")
        messages = st.get("messages") or {}
        print(f"\n--- line_item id={lid} status.name={name!r} ---")
        if messages.get("error"):
            print("  messages.error:", messages.get("error"))
        if messages.get("info"):
            print("  messages.info:", messages.get("info"))
        pn = messages.get("printable_normalization")
        if pn:
            print("  messages.printable_normalization:")
            print(json.dumps(pn, indent=4))
        if not messages:
            print("  (no status.messages)")


def cmd_print_job(api_base: str, token: str, job_id: str) -> None:
    url = f"{api_base}/print-jobs/{job_id}/"
    r = requests.get(url, headers=_auth_headers(token), timeout=120)
    print(f"GET {url}\nHTTP {r.status_code}")
    print(r.text)
    r.raise_for_status()
    job = r.json()
    print("\n=== Parsed: job status ===")
    print("  id:", job.get("id"), " status:", job.get("status"))
    print("  external_id:", job.get("external_id"))
    _extract_print_job_messages(job)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Lulu file validation & print-job debug (OpenAPI-aligned).")
    parser.add_argument(
        "--api-base",
        default=DEFAULT_API_BASE,
        help=f"Lulu API host (default {DEFAULT_API_BASE} or LULU_API_BASE)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_dim = sub.add_parser("cover-dimensions", help="Expected cover dimensions for POD + page count")
    p_dim.add_argument("--pod-package", default=DEFAULT_POD, help="POD package id")
    p_dim.add_argument("--pages", type=int, required=True, help="Interior page count (same as print job)")
    p_dim.add_argument("--unit", default="inch", choices=["inch", "mm", "pt"], help="Unit for width/height")

    p_cv = sub.add_parser("validate-cover", help="Async validate-cover + poll (same checks as normalization)")
    p_cv.add_argument("--url", dest="source_url", required=True, help="Public HTTPS URL to cover PDF")
    p_cv.add_argument("--pod-package", default=DEFAULT_POD)
    p_cv.add_argument("--pages", type=int, required=True)
    p_cv.add_argument("--poll-interval", type=float, default=3.0)
    p_cv.add_argument("--max-wait", type=float, default=600.0)

    p_int = sub.add_parser("validate-interior", help="Async validate-interior + poll")
    p_int.add_argument("--url", dest="source_url", required=True)
    p_int.add_argument("--pod-package", default=None, help="Optional; enables extended validation per OpenAPI")
    p_int.add_argument("--poll-interval", type=float, default=3.0)
    p_int.add_argument("--max-wait", type=float, default=600.0)

    p_pj = sub.add_parser("print-job", help="Fetch print job JSON and highlight line-item errors")
    p_pj.add_argument("job_id", help="Print job id (e.g. 2860285)")

    args = parser.parse_args()
    api_base = args.api_base.rstrip("/")
    token = get_lulu_token(api_base, *_client_creds())

    if args.command == "cover-dimensions":
        cmd_cover_dimensions(api_base, token, args.pod_package, args.pages, args.unit)
    elif args.command == "validate-cover":
        cmd_validate_cover(
            api_base,
            token,
            args.source_url,
            args.pod_package,
            args.pages,
            args.poll_interval,
            args.max_wait,
        )
    elif args.command == "validate-interior":
        cmd_validate_interior(
            api_base,
            token,
            args.source_url,
            args.pod_package,
            args.poll_interval,
            args.max_wait,
        )
    elif args.command == "print-job":
        cmd_print_job(api_base, token, str(args.job_id))
    else:
        parser.error("Unknown command")


if __name__ == "__main__":
    main()
