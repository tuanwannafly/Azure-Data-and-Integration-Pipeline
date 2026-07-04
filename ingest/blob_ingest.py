"""Azure Blob Storage ingest service (US-02).

Reads the raw JSON export produced by the SEC EDGAR adapter and uploads it to
the `raw/sec/` blob container with a timestamped/run-id name so re-runs never
overwrite a previous export (BR-02 idempotency: each upload is additive and
the downstream Copy Activity dedupes into the staging table).

Connection string is read from `AZURE_STORAGE_CONNECTION_STRING` (BR-01).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from azure.core.exceptions import AzureError
from azure.storage.blob import BlobServiceClient

from ingest.config import RAW_EXPORT_DIR

logger = logging.getLogger(__name__)

RAW_SEC_CONTAINER = "raw-sec"
MAX_UPLOAD_RETRIES = 3
RETRY_DELAY_SECONDS = 2.0


def _resolve_connection_string() -> str:
    """Read the mandatory storage connection string from env (BR-01)."""
    conn = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn or "REPLACE_ME" in conn:
        raise ValueError(
            "AZURE_STORAGE_CONNECTION_STRING env var is required to upload to Blob "
            "Storage. Set it in your .env (never committed)."
        )
    return conn


def build_blob_name(local_path: Path, *, run_id: str | None = None) -> str:
    """Build a unique blob name from a local export path.

    Format: sec/sec_export_<YYYYMMDD>_<runid>.json

    The local file is named ``raw_export_sec_<date>_<run>.json``; we normalize
    to the documented blob naming convention and virtual folder ``sec/``.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    rid = run_id or os.getenv("RUN_ID", "run01")
    return f"sec/sec_export_{stamp}_{rid}.json"


def upload_file(
    local_path: Path | str,
    *,
    container: str = RAW_SEC_CONTAINER,
    blob_name: str | None = None,
    client: BlobServiceClient | None = None,
    max_retries: int = MAX_UPLOAD_RETRIES,
    delay: float = RETRY_DELAY_SECONDS,
    run_id: str | None = None,
) -> str:
    """Upload one local file to blob storage with simple retry (BR-05).

    Args:
        local_path: path to the raw JSON export from the SEC adapter.
        container: target blob container (default raw-sec).
        blob_name: explicit blob name; defaults to a timestamped name.
        client: injected BlobServiceClient for testing; created from env if None.
        max_retries: number of upload attempts before giving up.
        delay: base seconds to wait between retries.
        run_id: optional run id used to build a unique blob name.

    Returns:
        The blob name that was written.
    """
    path = Path(local_path)
    if not path.is_file():
        raise FileNotFoundError(f"Cannot upload non-existent file: {path}")

    name = blob_name or build_blob_name(path, run_id=run_id)
    own_client = client is None
    svc = client or BlobServiceClient.from_connection_string(_resolve_connection_string())

    last_exc: Exception | None = None
    try:
        for attempt in range(1, max_retries + 1):
            try:
                blob_client = svc.get_blob_client(container=container, blob=name)
                with path.open("rb") as data:
                    blob_client.upload_blob(data, overwrite=True)
                logger.info(
                    "Uploaded %s -> container=%s blob=%s (attempt %s/%s)",
                    path.name, container, name, attempt, max_retries,
                )
                return name
            except AzureError as exc:
                last_exc = exc
                logger.warning(
                    "Upload attempt %s/%s failed for %s: %s",
                    attempt, max_retries, path.name, exc,
                )
                if attempt < max_retries:
                    time.sleep(delay * attempt)
        raise RuntimeError(f"Failed to upload {path.name} after {max_retries} attempts: {last_exc}")
    finally:
        if own_client:
            svc.close()


def upload_latest_export(
    export_dir: str | None = None,
    *,
    container: str = RAW_SEC_CONTAINER,
    client: BlobServiceClient | None = None,
) -> str:
    """Find the newest raw_export_sec_*.json in export_dir and upload it.

    Returns the blob name written. Raises FileNotFoundError if no export exists.
    """
    search_dir = Path(export_dir or RAW_EXPORT_DIR)
    candidates = sorted(search_dir.glob("raw_export_sec_*.json"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        raise FileNotFoundError(f"No raw_export_sec_*.json found in {search_dir}")
    return upload_file(candidates[-1], container=container, client=client)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    uploaded = upload_latest_export()
    print(f"Uploaded blob: {uploaded}")
