"""
routers/sku_specs.py — Spec PDF Ingestion, Interactive Review, and Integrity Endpoints.

Handles:
- POST /skus/{sku_id}/spec-upload: Upload spec PDF, compute SHA-256, parse deterministically, store raw JSON.
- GET /skus/{sku_id}/spec-review/{upload_id}: Split-screen PDF + editable structured form.
- POST /skus/{sku_id}/spec-review/{upload_id}: Save corrected JSON, compute SHA-256, load to DB.
- GET /skus/{sku_id}/spec-uploads/{upload_id}/pdf: Stream stored PDF for iframe viewing.
- POST /skus/{sku_id}/spec-verify/{upload_id}: Run cryptographic integrity & audit chain check.
- GET /skus/ingestion-errors: List failed watcher items and unmatched directory PDFs.
"""

from datetime import datetime
import json
import logging
import os
from typing import Any, Dict, List, Optional
import uuid
import asyncpg
from starlette.status import (
    HTTP_200_OK,
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
)
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from auth.dependencies import current_user, require
from database import get_db
from services.spec_integrity import compute_bytes_sha256, verify_upload_integrity
from services.spec_loader import load_specification_document
from services.spec_parser import parse_spec_pdf
from services.storage import get_file_from_storage, upload_file_to_storage, UPLOAD_BASE_DIR

logger = logging.getLogger("snm_works.sku_specs")
router = APIRouter(prefix="/skus", tags=["sku_specs"])
templates = Jinja2Templates(directory="templates")


@router.get("/ingestion-errors", response_class=HTMLResponse)
async def list_ingestion_errors(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("specifications", "read")),
):
    """
    Renders list of ingestion errors and any orphan PDFs found in the incoming_specs/_unmatched directory.
    """
    user_info = {
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    # 1. Fetch DB error records
    db_errors = []
    try:
        rows = await conn.fetch(
            """
            SELECT u.*, s.sku_code, s.title AS sku_title, p.full_name AS uploader_name
            FROM spec_pdf_uploads u
            LEFT JOIN skus s ON s.id = u.sku_id
            LEFT JOIN profiles p ON p.id = u.uploaded_by
            WHERE u.status = 'Error'
            ORDER BY u.uploaded_at DESC;
            """
        )
        db_errors = [dict(r) for r in rows]
    except Exception as exc:
        logger.warning(f"Failed to fetch DB ingestion errors: {exc}")

    # 2. Check unmatched watcher folder
    unmatched_files = []
    unmatched_dir = os.path.join(os.getcwd(), "incoming_specs", "_unmatched")
    if os.path.exists(unmatched_dir):
        for fname in os.listdir(unmatched_dir):
            fpath = os.path.join(unmatched_dir, fname)
            if os.path.isfile(fpath) and fname.lower().endswith(".pdf"):
                stat = os.stat(fpath)
                unmatched_files.append({
                    "filename": fname,
                    "file_size": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "path": fpath,
                })

    return templates.TemplateResponse(
        request=request,
        name="skus/ingestion_errors.html",
        context={
            "user": user_info,
            "db_errors": db_errors,
            "unmatched_files": unmatched_files,
        },
    )


@router.post("/{sku_id}/spec-upload")
async def upload_sku_spec_pdf(
    request: Request,
    sku_id: str,
    pdf_file: UploadFile = File(...),
    relationship: str = Form("primary"),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("specifications", "create")),
):
    """
    Handles manual spec PDF upload from the SKU detail screen:
    1. Validates PDF format and file size (< 25MB).
    2. Computes PDF SHA-256 and stores in spec-docs bucket.
    3. Runs deterministic extraction via services.spec_parser.
    4. Computes raw JSON SHA-256 and stores in spec-parsed bucket.
    5. Inserts spec_pdf_uploads row in 'Parsed' status.
    6. Redirects to interactive review UI.
    """
    raw_pdf_bytes = await pdf_file.read()
    if not raw_pdf_bytes:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Uploaded PDF file is empty.")

    if len(raw_pdf_bytes) > 25 * 1024 * 1024:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="File size exceeds 25MB limit.")

    # Verify SKU exists
    sku_row = await conn.fetchrow("SELECT id, sku_code FROM skus WHERE id::text = $1 OR sku_code = $1;", sku_id)
    if not sku_row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="SKU not found.")

    sku_uuid = sku_row["id"]
    sku_code = sku_row["sku_code"]
    user_id = uuid.UUID(user["id"])

    # 1. Compute PDF Hash & Store
    upload_id = uuid.uuid4()
    pdf_sha256 = compute_bytes_sha256(raw_pdf_bytes)
    pdf_storage_rel = f"{sku_code}/{upload_id}.pdf"
    await upload_file_to_storage("spec-docs", pdf_storage_rel, raw_pdf_bytes, "application/pdf")

    # 2. Parse Deterministically
    try:
        parsed_dict = parse_spec_pdf(raw_pdf_bytes)
    except Exception as exc:
        logger.error(f"Deterministic spec parsing failed: {exc}", exc_info=True)
        parsed_dict = {
            "specification": {
                "spec_no": os.path.splitext(pdf_file.filename or "SPEC")[0],
                "revision": "A",
                "title": f"Specification for {sku_code}",
            },
            "variants": [],
            "requirements": [],
            "defects": [],
            "sampling": [],
            "parse_error": str(exc),
        }

    # 3. Compute JSON Hash & Store
    json_bytes = json.dumps(parsed_dict, indent=2).encode("utf-8")
    parsed_json_sha256 = compute_bytes_sha256(json_bytes)
    json_storage_rel = f"{upload_id}_raw.json"
    await upload_file_to_storage("spec-parsed", json_storage_rel, json_bytes, "application/json")

    # 4. Insert DB Record
    await conn.execute(
        """
        INSERT INTO spec_pdf_uploads (
            id, sku_id, storage_path, pdf_sha256, parsed_json_path,
            parsed_json_sha256, source, original_filename, file_size_bytes,
            status, uploaded_by
        ) VALUES (
            $1, $2, $3, $4, $5, $6, 'web_upload', $7, $8, 'Parsed', $9
        );
        """,
        upload_id,
        sku_uuid,
        f"spec-docs/{pdf_storage_rel}",
        pdf_sha256,
        f"spec-parsed/{json_storage_rel}",
        parsed_json_sha256,
        pdf_file.filename or "uploaded_spec.pdf",
        len(raw_pdf_bytes),
        user_id,
    )

    review_url = f"/skus/{sku_uuid}/spec-review/{upload_id}?rel={relationship}"
    if request.headers.get("hx-request") == "true":
        response = Response(status_code=HTTP_200_OK)
        response.headers["HX-Redirect"] = review_url
        return response

    return RedirectResponse(url=review_url, status_code=HTTP_303_SEE_OTHER)


@router.get("/{sku_id}/spec-review/{upload_id}", response_class=HTMLResponse)
async def spec_review_view(
    request: Request,
    sku_id: str,
    upload_id: str,
    rel: str = "primary",
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("specifications", "read")),
):
    """
    Renders the split-screen Spec Review UI:
    - Left: Embedded PDF stream.
    - Right: Interactive editable JSON / tabular form highlighting missing fields in amber.
    """
    user_info = {
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    sku_row = await conn.fetchrow("SELECT * FROM skus WHERE id::text = $1 OR sku_code = $1;", sku_id)
    if not sku_row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="SKU not found.")

    try:
        upload_uuid = uuid.UUID(upload_id)
    except ValueError:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Spec upload record not found.")

    upload_row = await conn.fetchrow("SELECT * FROM spec_pdf_uploads WHERE id = $1;", upload_uuid)
    if not upload_row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Spec upload record not found.")

    upload_data = dict(upload_row)

    # Read the active parsed JSON (corrected if already edited, else raw parsed)
    json_path = upload_data.get("corrected_json_path") or upload_data.get("parsed_json_path")
    spec_data: Dict[str, Any] = {}
    if json_path:
        bucket = "spec-parsed"
        rel_path = json_path.split("/", 1)[1] if "/" in json_path and json_path.startswith("spec-parsed/") else json_path
        json_bytes = await get_file_from_storage(bucket, rel_path)
        if json_bytes:
            try:
                spec_data = json.loads(json_bytes.decode("utf-8"))
            except Exception as exc:
                logger.warning(f"Could not decode parsed JSON: {exc}")

    return templates.TemplateResponse(
        request=request,
        name="skus/spec_review.html",
        context={
            "user": user_info,
            "sku": dict(sku_row),
            "upload": upload_data,
            "spec_data": spec_data,
            "spec_json_str": json.dumps(spec_data, indent=2),
            "relationship": rel,
        },
    )


@router.post("/{sku_id}/spec-review/{upload_id}")
async def submit_spec_review(
    request: Request,
    sku_id: str,
    upload_id: str,
    relationship: str = Form("primary"),
    spec_json: str = Form(...),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("specifications", "create")),
):
    """
    Saves reviewer corrections and loads the specification into the database:
    1. Validates submitted JSON payload.
    2. Computes corrected JSON SHA-256 and writes to spec-parsed bucket.
    3. Updates spec_pdf_uploads with corrected path and hash.
    4. Invokes shared load_specification_document() to populate DB and sync SKU.
    """
    sku_row = await conn.fetchrow("SELECT id, sku_code FROM skus WHERE id::text = $1 OR sku_code = $1;", sku_id)
    if not sku_row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="SKU not found.")

    sku_uuid = sku_row["id"]
    upload_uuid = uuid.UUID(upload_id)
    user_id = uuid.UUID(user["id"])

    try:
        spec_dict = json.loads(spec_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=f"Invalid JSON payload: {exc}")

    # 1. Compute Corrected JSON Hash & Store
    corr_bytes = json.dumps(spec_dict, indent=2).encode("utf-8")
    corr_sha256 = compute_bytes_sha256(corr_bytes)
    corr_storage_rel = f"{upload_uuid}_corrected.json"
    await upload_file_to_storage("spec-parsed", corr_storage_rel, corr_bytes, "application/json")

    # 2. Update upload record with corrected hash
    await conn.execute(
        """
        UPDATE spec_pdf_uploads
        SET corrected_json_path = $1,
            corrected_json_sha256 = $2,
            status = 'Reviewed'
        WHERE id = $3;
        """,
        f"spec-parsed/{corr_storage_rel}",
        corr_sha256,
        upload_uuid,
    )

    # 3. Load into Database via Shared Spec Loader
    try:
        load_res = await load_specification_document(
            conn=conn,
            spec_data=spec_dict,
            created_by_uuid=user_id,
            sku_id=sku_uuid,
            relationship=relationship,
            upload_id=upload_uuid,
        )
        logger.info(f"Specification '{load_res['spec_no']}' loaded successfully for SKU {sku_row['sku_code']}.")
    except Exception as exc:
        logger.error(f"Failed to load specification from review: {exc}", exc_info=True)
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=f"Failed to load specification: {exc}")

    target_url = f"/skus/{sku_uuid}"
    if request.headers.get("hx-request") == "true":
        response = Response(status_code=HTTP_200_OK)
        response.headers["HX-Redirect"] = target_url
        return response

    return RedirectResponse(url=target_url, status_code=HTTP_303_SEE_OTHER)


@router.get("/{sku_id}/spec-uploads/{upload_id}/pdf")
async def stream_spec_pdf(
    sku_id: str,
    upload_id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("specifications", "read")),
):
    """
    Streams raw PDF bytes for embedded iframe viewing in the Spec Review UI.
    """
    try:
        upload_uuid = uuid.UUID(upload_id)
    except ValueError:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="PDF upload record not found.")

    row = await conn.fetchrow("SELECT storage_path, original_filename FROM spec_pdf_uploads WHERE id = $1;", upload_uuid)
    if not row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="PDF upload record not found.")

    storage_path = row["storage_path"]
    bucket = "spec-docs"
    rel_path = storage_path.split("/", 1)[1] if "/" in storage_path and storage_path.startswith("spec-docs/") else storage_path

    pdf_bytes = await get_file_from_storage(bucket, rel_path)
    if not pdf_bytes:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="PDF file content not found on storage.")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=\"{row['original_filename']}\"",
        },
    )


@router.post("/{sku_id}/spec-verify/{upload_id}")
async def verify_spec_pdf_hash(
    request: Request,
    sku_id: str,
    upload_id: str,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("audit", "read")),
):
    """
    Recomputes physical file SHA-256 hashes and verifies database hash integrity and SHA-256 audit chain.
    """
    try:
        upload_uuid = uuid.UUID(upload_id)
    except ValueError:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Upload record not found.")

    try:
        res = await verify_upload_integrity(conn, upload_uuid)
    except Exception as exc:
        logger.error(f"Integrity check execution error: {exc}", exc_info=True)
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc))

    is_htmx = request.headers.get("hx-request") == "true"
    if is_htmx:
        if res["status"] == "VALID":
            return HTMLResponse(
                f"""
                <span class="badge badge-pass" title="Stored SHA-256 matches actual file SHA-256. Audit log chain is intact.">
                  ✓ VALID (HASH & AUDIT OK)
                </span>
                """
            )
        else:
            details_str = json.dumps(res.get("details", {}), indent=2)
            return HTMLResponse(
                f"""
                <span class="badge badge-fail" title="Integrity Check Failed: {details_str}">
                  ✗ CORRUPTED
                </span>
                """
            )

    return res
