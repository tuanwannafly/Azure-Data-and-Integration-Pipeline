#!/usr/bin/env python
"""Idempotently upload (or overwrite) a Python source file as a Databricks
notebook using the Workspace Import-Notebook REST API.

Usage:
    python scripts/databricks_upload_notebook.py \
        --host https://adb-xxx.azuredatabricks.net \
        --token dapi... \
        --path /Shared/transform_revenue \
        --source /tmp/transform_revenue_clean.py
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request


def import_notebook(
    host: str,
    token: str,
    path: str,
    source: str,
    language: str = "PYTHON",
) -> dict:
    """POST /api/2.0/workspace/import with `overwrite=true` for idempotency."""
    url = f"{host.rstrip('/')}/api/2.0/workspace/import"
    payload = {
        "path": path,
        "language": language,
        "format": "SOURCE",
        "content": base64.b64encode(source.encode("utf-8")).decode("ascii"),
        "overwrite": True,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}: {body}", file=sys.stderr)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Workspace URL, e.g. https://adb-xxx.azuredatabricks.net")
    parser.add_argument("--token", required=True, help="Personal access token (or OIDC token)")
    parser.add_argument("--path", required=True, help="Workspace path, e.g. /Shared/transform_revenue")
    parser.add_argument("--source", required=True, type=open, help="Python source file")
    parser.add_argument("--language", default="PYTHON")
    args = parser.parse_args()

    source = args.source.read()
    args.source.close()
    result = import_notebook(args.host, args.token, args.path, source, args.language)
    print(f"imported notebook -> {args.path}")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
