"""
tests/test_spec_bridge.py — Integration Tests for Spec Review Bridge & Standalone Review UI.

Tests:
1. Standalone review UI rendering: GET /skus/standalone/spec-review/{upload_id}
2. Standalone PDF streaming: GET /skus/standalone/spec-uploads/{upload_id}/pdf
3. Standalone review submission & loading: POST /skus/standalone/spec-review/{upload_id}
4. Verifies specification records are created 100% standalone without SKU linkage.
"""

import hashlib
import json
import os
import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from jose import jwt

from config import settings
from main import app
from database import init_db_pool, get_db
from services.storage import UPLOAD_BASE_DIR


@pytest.fixture
def auth_cookies():
    user_id = uuid.UUID("44444444-4444-4444-4444-444444444444")
    secret = settings.supabase_jwt_secret or settings.secret_key or "snm-works-super-secret-key-change-in-production"
    token = jwt.encode(
        {"sub": str(user_id), "email": "chief.quality@snmills.com", "role": "authenticated"},
        secret,
        algorithm="HS256",
    )
    return {"access_token": token}


@pytest.mark.asyncio
async def test_standalone_spec_review_and_stream(auth_cookies):
    """
    Tests that a standalone specification upload (unlinked to commercial SKU)
    can stream its PDF and render the split-screen review form without errors.
    """
    pool = await init_db_pool()
    assert pool is not None

    async with pool.acquire() as conn:
        # Create test upload record
        test_upload_id = uuid.uuid4()
        mock_pdf = b"%PDF-1.4 Mock standalone technical specification content for review testing."
        pdf_sha = hashlib.sha256(mock_pdf).hexdigest()

        # Write to local cache
        pdf_dir = os.path.join(UPLOAD_BASE_DIR, "spec-docs", "standalone")
        os.makedirs(pdf_dir, exist_ok=True)
        with open(os.path.join(pdf_dir, f"{test_upload_id}.pdf"), "wb") as f:
            f.write(mock_pdf)

        mock_spec_json = {
            "specification": {
                "spec_no": "TEST-STANDALONE-01",
                "revision": "R0",
                "title": "Test Standalone Nylon Webbing Spec",
                "issuing_body": "INDIAN DEFENCE",
            },
            "variants": [
                {"key": "Default", "designation": "Standard", "class": "1", "sort_order": 1}
            ],
            "requirements": [
                {
                    "parameter": "Breaking strength",
                    "unit": "kgf",
                    "limit_type": "minimum",
                    "spec_value": 250.0,
                    "tolerance": None,
                    "variant_keys": ["Default"],
                    "is_critical": True,
                    "sort_order": 1,
                }
            ],
            "defects": [],
            "sampling": [],
        }
        json_bytes = json.dumps(mock_spec_json, indent=2).encode("utf-8")
        json_sha = hashlib.sha256(json_bytes).hexdigest()

        json_dir = os.path.join(UPLOAD_BASE_DIR, "spec-parsed")
        os.makedirs(json_dir, exist_ok=True)
        with open(os.path.join(json_dir, f"{test_upload_id}_raw.json"), "wb") as f:
            f.write(json_bytes)

        # Get uploader & ref_sku_id
        uploader_id = uuid.UUID("44444444-4444-4444-4444-444444444444")
        from auth.middleware import set_rls_claims

        async with conn.transaction():
            await set_rls_claims(conn, {"sub": str(uploader_id), "email": "chief.quality@snmills.com", "role": "authenticated"})
            ref_sku_id = await conn.fetchval("SELECT id FROM skus WHERE sku_code = 'REF-SPECS';")

            await conn.execute("""
                INSERT INTO spec_pdf_uploads (
                    id, sku_id, storage_path, pdf_sha256, parsed_json_path,
                    parsed_json_sha256, source, original_filename, file_size_bytes,
                    status, uploaded_by
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, 'folder_watcher', 'TEST_SPEC.pdf', $7, 'Parsed', $8
                ) ON CONFLICT (id) DO UPDATE SET status = 'Parsed';
            """,
                test_upload_id,
                ref_sku_id,
                f"spec-docs/standalone/{test_upload_id}.pdf",
                pdf_sha,
                f"spec-parsed/{test_upload_id}_raw.json",
                json_sha,
                len(mock_pdf),
                uploader_id,
            )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver", cookies=auth_cookies) as client:
            # 1. Test PDF streaming endpoint
            stream_resp = await client.get(f"/skus/standalone/spec-uploads/{test_upload_id}/pdf")
            assert stream_resp.status_code == 200
            assert stream_resp.headers["content-type"] == "application/pdf"
            assert stream_resp.content == mock_pdf

            # 2. Test split-screen review view
            review_resp = await client.get(f"/skus/standalone/spec-review/{test_upload_id}")
            assert review_resp.status_code == 200
            html = review_resp.text
            assert "TEST-STANDALONE-01" in html
            assert "STANDALONE REFERENCE" in html
            assert f"/skus/standalone/spec-uploads/{test_upload_id}/pdf" in html

            # 3. Test review submission & loading
            # Yash confirms the spec and loads it into database
            submit_data = {
                "relationship": "reference",
                "spec_json": json.dumps(mock_spec_json),
            }
            sub_resp = await client.post(
                f"/skus/standalone/spec-review/{test_upload_id}",
                data=submit_data,
                follow_redirects=False,
            )
            assert sub_resp.status_code in (302, 303)
            redirect_target = sub_resp.headers["location"]
            assert "/specifications/" in redirect_target

            # 4. Verify specification is created in DB as standalone
            async with conn.transaction():
                await set_rls_claims(conn, {"sub": str(uploader_id), "email": "yashkhandelwal95@gmail.com", "role": "authenticated"})
                spec_row = await conn.fetchrow(
                    "SELECT * FROM specifications WHERE spec_no = $1;",
                    "TEST-STANDALONE-01",
                )
                assert spec_row is not None
                assert spec_row["title"] == "Test Standalone Nylon Webbing Spec"

                # 5. Verify NO rows in sku_specifications linking to any commercial SKU
                sku_links = await conn.fetch(
                    "SELECT * FROM sku_specifications WHERE spec_id = $1;",
                    spec_row["id"],
                )
                assert len(sku_links) == 0, "Standalone specification must have 0 commercial SKU links"

                # 6. Verify upload record status updated to 'Loaded'
                updated_upload = await conn.fetchrow(
                    "SELECT status, spec_id FROM spec_pdf_uploads WHERE id = $1;",
                    test_upload_id,
                )
                assert updated_upload["status"] == "Loaded"
                assert updated_upload["spec_id"] == spec_row["id"]

    await pool.close()
