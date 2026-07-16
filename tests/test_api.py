from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from api import create_app
from document_processing.config import AppConfig


PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF"


class FastProcessor:
    def health(self) -> dict[str, Any]:
        return {
            "ocr": {"ready": True, "engine": "test"},
            "extractor": {"ready": True},
            "visualizer": {"ready": True},
        }

    def process_document(
        self,
        *,
        job_dir: Path,
        document_id: str,
        source_path: Path,
        source_file_name: str,
    ) -> dict[str, Any]:
        assert source_path.read_bytes().startswith(b"%PDF-")
        document_dir = job_dir / "documents" / document_id
        result_path = document_dir / "results" / "result.json"
        annotated_path = document_dir / "annotated" / "annotated.pdf"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        annotated_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(
                {
                    "document_id": document_id,
                    "source_file_name": source_file_name,
                    "status": "completed",
                    "ocr_result": {"text": "test", "pages": [{}]},
                    "extracted_entities": [
                        {
                            "type": "bin",
                            "value": "123456789012",
                            "confidence": 0.99,
                            "locations": [
                                {"page": 1, "bbox": [0.1, 0.2, 0.3, 0.4]}
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        annotated_path.write_bytes(PDF_BYTES)
        return {
            "status": "completed",
            "page_count": 1,
            "entity_count": 1,
            "entities": [
                {
                    "type": "bin",
                    "value": "123456789012",
                    "confidence": 0.99,
                    "locations": [
                        {"page": 1, "bbox": [0.1, 0.2, 0.3, 0.4]}
                    ],
                }
            ],
            "warnings": [],
            "errors": [],
            "output_files": {
                "result_json": str(result_path.relative_to(job_dir)),
                "annotated_document": str(
                    annotated_path.relative_to(job_dir)
                ),
            },
            "timing": {"total_seconds": 0.01},
        }


@pytest.fixture
def client(tmp_path: Path):
    config = AppConfig(
        data_dir=tmp_path / "data",
        frontend_dir=tmp_path / "frontend",
        max_files=3,
        job_workers=2,
    )
    app = create_app(config, processor=FastProcessor())
    with TestClient(app) as test_client:
        yield test_client


def wait_for_job(client: TestClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in {
            "completed",
            "partially_completed",
            "failed",
        }:
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def submit_pdf(
    client: TestClient,
    name: str = "contract.pdf",
) -> dict[str, Any]:
    response = client.post(
        "/api/documents/process",
        files=[("files", (name, PDF_BYTES, "application/pdf"))],
    )
    assert response.status_code == 202
    return response.json()


def test_health_reports_components(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_process_and_download_all_outputs(client: TestClient) -> None:
    submitted = submit_pdf(client)
    job = wait_for_job(client, submitted["job_id"])

    assert job["status"] == "completed"
    document = job["documents"][0]
    assert document["page_count"] == 1
    assert document["entity_count"] == 1

    pdf_response = client.get(document["downloads"]["annotated_document"])
    json_response = client.get(document["downloads"]["result_json"])
    zip_response = client.get(job["download_url"])

    assert pdf_response.status_code == 200
    assert pdf_response.content.startswith(b"%PDF-")
    assert json_response.status_code == 200
    assert json_response.json()["document_id"] == document["document_id"]
    assert zip_response.status_code == 200


def test_duplicate_names_are_isolated_in_batch(
    client: TestClient,
    tmp_path: Path,
) -> None:
    response = client.post(
        "/api/documents/process",
        files=[
            ("files", ("same.pdf", PDF_BYTES, "application/pdf")),
            ("files", ("same.pdf", PDF_BYTES, "application/pdf")),
        ],
    )
    job = wait_for_job(client, response.json()["job_id"])

    assert job["status"] == "completed"
    assert len({item["document_id"] for item in job["documents"]}) == 2
    archive_response = client.get(job["download_url"])
    archive_path = tmp_path / "results.zip"
    archive_path.write_bytes(archive_response.content)
    with ZipFile(archive_path) as archive:
        folders = {
            item.split("/", 1)[0]
            for item in archive.namelist()
            if "/" in item
        }
    assert len(folders) == 2


def test_one_invalid_file_does_not_break_batch(client: TestClient) -> None:
    response = client.post(
        "/api/documents/process",
        files=[
            ("files", ("good.pdf", PDF_BYTES, "application/pdf")),
            ("files", ("bad.txt", b"text", "text/plain")),
        ],
    )
    job = wait_for_job(client, response.json()["job_id"])

    assert job["status"] == "partially_completed"
    assert {item["status"] for item in job["documents"]} == {
        "completed",
        "failed",
    }
    failed = next(
        item for item in job["documents"] if item["status"] == "failed"
    )
    assert failed["errors"][0]["code"] == "UNSUPPORTED_FORMAT"


def test_empty_or_corrupted_upload_has_per_file_error(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/documents/process",
        files=[("files", ("broken.pdf", b"broken", "application/pdf"))],
    )
    job = wait_for_job(client, response.json()["job_id"])

    assert job["status"] == "failed"
    assert job["documents"][0]["errors"][0]["code"] == "INVALID_PDF"


def test_repeated_and_parallel_jobs_are_isolated(client: TestClient) -> None:
    first = wait_for_job(client, submit_pdf(client, "first.pdf")["job_id"])
    second = wait_for_job(client, submit_pdf(client, "second.pdf")["job_id"])
    assert first["job_id"] != second["job_id"]

    def submit(name: str) -> str:
        return submit_pdf(client, name)["job_id"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        job_ids = list(executor.map(submit, ["tab-a.pdf", "tab-b.pdf"]))

    parallel_jobs = [wait_for_job(client, job_id) for job_id in job_ids]
    assert all(job["status"] == "completed" for job in parallel_jobs)
    assert len(set(job_ids)) == 2
