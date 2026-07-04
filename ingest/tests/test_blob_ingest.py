"""Unit tests for blob_ingest.py (US-02).

Mock BlobServiceClient so tests run without an Azure Storage account.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from azure.core.exceptions import AzureError

from ingest.blob_ingest import build_blob_name, upload_file


def _write_sample(tmp_path: Path) -> Path:
    payload = {
        "exported_at": "2026-07-02T00:00:00+00:00",
        "records": [{"cik": 320193}],
    }
    path = tmp_path / "raw_export_sec_20260702_run01.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_build_blob_name_uses_documented_convention(tmp_path):
    path = tmp_path / "raw_export_sec_20260702_run01.json"
    name = build_blob_name(path, run_id="run01")
    assert name.startswith("sec/sec_export_")
    assert name.endswith("_run01.json")


@patch("ingest.blob_ingest.BlobServiceClient")
def test_upload_file_calls_upload_blob_with_correct_args(mock_client_cls, tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", "UseDevelopmentStorage=true")
    local_path = _write_sample(tmp_path)

    mock_client = MagicMock()
    mock_blob = MagicMock()
    mock_client.get_blob_client.return_value = mock_blob
    mock_client_cls.from_connection_string.return_value = mock_client

    # Capture the uploaded bytes as the file is streamed into upload_blob.
    captured = {}

    def _capture(handle, *args, **kwargs):
        captured["data"] = handle.read()

    mock_blob.upload_blob.side_effect = _capture

    blob_name = upload_file(local_path, container="raw-sec", run_id="run01")

    assert blob_name.startswith("sec/sec_export_")
    mock_client.get_blob_client.assert_called_once()
    mock_blob.upload_blob.assert_called_once()

    # Verify the uploaded bytes are the raw file content (no re-serialization).
    assert json.loads(captured["data"])["records"][0]["cik"] == 320193


@patch("ingest.blob_ingest.BlobServiceClient")
def test_upload_file_retries_on_failure(mock_client_cls, tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", "UseDevelopmentStorage=true")
    local_path = _write_sample(tmp_path)

    mock_blob = MagicMock()
    mock_blob.upload_blob.side_effect = [AzureError("transient"), MagicMock()]
    mock_client = MagicMock()
    mock_client.get_blob_client.return_value = mock_blob
    mock_client_cls.from_connection_string.return_value = mock_client

    blob_name = upload_file(local_path, container="raw-sec", max_retries=3)

    assert mock_blob.upload_blob.call_count == 2
    assert blob_name.startswith("sec/sec_export_")


def test_upload_file_raises_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", "UseDevelopmentStorage=true")
    import pytest

    with pytest.raises(FileNotFoundError):
        upload_file(tmp_path / "does_not_exist.json")
