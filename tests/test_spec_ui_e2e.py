"""
tests/test_spec_ui_e2e.py — End-to-End Tests for Spec PDF Upload, Split-Screen Review, & Verification UI.

Tests:
1. Upload spec PDF via POST /skus/{sku_id}/spec-upload
2. Stream embedded spec PDF via GET /skus/{sku_id}/spec-uploads/{upload_id}/pdf
3. Split-screen review page rendering via GET /skus/{sku_id}/spec-review/{upload_id}
4. Review confirmation & loading via POST /skus/{sku_id}/spec-review/{upload_id}
5. Ingestion errors queue via GET /skus/ingestion-errors
6. Cryptographic integrity check via POST /skus/{sku_id}/spec-verify/{upload_id}
"""

import json
import os
import uuid
import pytest
import asyncpg
from httpx import AsyncClient, ASGITransport
from jose import jwt
from config import settings
from main import app


@pytest.mark.asyncio
async def test_spec_ingestion_ui_workflow_e2e():
    conn = await asyncpg.connect("postgresql://postgres@127.0.0.1:5433/snm_test_db")
    try:
        # Create user with chief_quality role (holds specifications:create, specifications:read, skus:read, audit:read)
        user_id = uuid.UUID("55555555-5555-5555-5555-555555555555")
        user_email = "quality.e2e@snmills.com"
        await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING;", user_id, user_email)
        await conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1, 'Quality E2E User', 'owner', true) ON CONFLICT (id) DO UPDATE SET active = true;", user_id)
        await conn.execute("INSERT INTO user_roles (user_id, role_code, active) VALUES ($1, 'chief_quality', true) ON CONFLICT (user_id, role_code) DO UPDATE SET active = true;", user_id)

        # Create test SKU
        sku_id = uuid.uuid4()
        sku_code = f"E2E-SKU-{uuid.uuid4().hex[:6].upper()}"
        await conn.execute(
            """
            INSERT INTO skus (id, sku_code, family, title, status)
            VALUES ($1, $2, 'Narrow woven', 'E2E Webbing SKU', 'Draft');
            """,
            sku_id,
            sku_code,
        )

        secret = settings.supabase_jwt_secret or settings.secret_key or "snm-works-super-secret-key-change-in-production"
        jwt_token = jwt.encode({"sub": str(user_id), "email": user_email, "role": "authenticated"}, secret, algorithm="HS256")
        cookies = {"access_token": jwt_token}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver", cookies=cookies) as client:
            
            # -------------------------------------------------------------------
            # Step 1: Upload Spec PDF (POST /skus/{sku_id}/spec-upload)
            # -------------------------------------------------------------------
            mock_pdf_content = b"%PDF-1.4 Mock military specification document for E2E ingestion testing."
            files = {
                "pdf_file": ("MIL-W-4088K_E2E.pdf", mock_pdf_content, "application/pdf")
            }
            data = {
                "relationship": "primary"
            }
            upload_resp = await client.post(
                f"/skus/{sku_id}/spec-upload",
                files=files,
                data=data,
                follow_redirects=False,
            )
            assert upload_resp.status_code in (302, 303, 200)
            redirect_url = upload_resp.headers.get("Location") or upload_resp.headers.get("HX-Redirect")
            assert "/spec-review/" in redirect_url
            upload_id_str = redirect_url.split("/spec-review/")[1].split("?")[0]
            upload_id = uuid.UUID(upload_id_str)

            # Check DB record was inserted in 'Parsed' status
            upload_row = await conn.fetchrow("SELECT * FROM spec_pdf_uploads WHERE id = $1;", upload_id)
            assert upload_row is not None
            assert upload_row["status"] == "Parsed"
            assert upload_row["source"] == "web_upload"
            assert upload_row["original_filename"] == "MIL-W-4088K_E2E.pdf"

            # -------------------------------------------------------------------
            # Step 2: Stream PDF (GET /skus/{sku_id}/spec-uploads/{upload_id}/pdf)
            # -------------------------------------------------------------------
            pdf_stream_resp = await client.get(f"/skus/{sku_id}/spec-uploads/{upload_id}/pdf")
            assert pdf_stream_resp.status_code == 200
            assert pdf_stream_resp.headers["content-type"] == "application/pdf"
            assert pdf_stream_resp.content == mock_pdf_content

            # -------------------------------------------------------------------
            # Step 3: View Review Screen (GET /skus/{sku_id}/spec-review/{upload_id})
            # -------------------------------------------------------------------
            review_page_resp = await client.get(f"/skus/{sku_id}/spec-review/{upload_id}")
            assert review_page_resp.status_code == 200
            assert "Review Spec PDF" in review_page_resp.text
            assert sku_code in review_page_resp.text
            assert f"/skus/{sku_id}/spec-uploads/{upload_id}/pdf" in review_page_resp.text

            # -------------------------------------------------------------------
            # Step 4: Submit Review & Load Spec (POST /skus/{sku_id}/spec-review/{upload_id})
            # -------------------------------------------------------------------
            spec_no = f"MIL-W-4088K-E2E-{uuid.uuid4().hex[:4].upper()}"
            reviewed_spec_dict = {
                "specification": {
                    "spec_no": spec_no,
                    "revision": "K",
                    "title": "WEBBING, TEXTILE, WOVEN NYLON",
                    "issuing_body": "Department of Defense",
                    "issued_on": "2026-09-01",
                },
                "variants": [
                    {
                        "key": "VIII-C1",
                        "designation": "Type VIII",
                        "class": "1",
                        "description": "Heavy parachute webbing",
                        "sort_order": 1,
                    }
                ],
                "requirements": [
                    {
                        "variant_keys": ["VIII-C1"],
                        "parameter": "Breaking strength",
                        "unit": "lb",
                        "limit_type": "minimum",
                        "spec_value": 4000.0,
                        "tolerance": None,
                        "test_method": "ASTM D3774",
                        "clause_ref": "3.6.1",
                        "is_critical": True,
                        "sort_order": 1,
                    }
                ],
                "defects": [],
                "sampling": []
            }

            submit_resp = await client.post(
                f"/skus/{sku_id}/spec-review/{upload_id}",
                data={
                    "relationship": "primary",
                    "spec_json": json.dumps(reviewed_spec_dict),
                },
                follow_redirects=False,
            )
            assert submit_resp.status_code in (302, 303, 200)

            # Confirm upload record transitioned to 'Loaded'
            updated_upload = await conn.fetchrow("SELECT * FROM spec_pdf_uploads WHERE id = $1;", upload_id)
            assert updated_upload["status"] == "Loaded"
            assert updated_upload["corrected_json_sha256"] is not None
            assert updated_upload["spec_id"] is not None

            # Confirm sku_specifications junction row created and SKU standard synced
            sku_spec_row = await conn.fetchrow(
                "SELECT * FROM sku_specifications WHERE sku_id = $1 AND spec_id = $2;",
                sku_id,
                updated_upload["spec_id"],
            )
            assert sku_spec_row is not None
            assert sku_spec_row["is_primary"] is True

            updated_sku = await conn.fetchrow("SELECT standard FROM skus WHERE id = $1;", sku_id)
            assert spec_no in updated_sku["standard"]
            assert "Type VIII Class 1" in updated_sku["standard"]

            # -------------------------------------------------------------------
            # Step 5: Cryptographic Integrity Verification Endpoint (POST /skus/{sku_id}/spec-verify/{upload_id})
            # -------------------------------------------------------------------
            verify_resp = await client.post(
                f"/skus/{sku_id}/spec-verify/{upload_id}",
                headers={"HX-Request": "true"},
            )
            assert verify_resp.status_code == 200
            assert "VALID" in verify_resp.text
            assert "badge-pass" in verify_resp.text

            # -------------------------------------------------------------------
            # Step 6: Ingestion Errors Queue (GET /skus/ingestion-errors)
            # -------------------------------------------------------------------
            errors_resp = await client.get("/skus/ingestion-errors")
            assert errors_resp.status_code == 200
            assert "Specification PDF Ingestion Queue & Errors" in errors_resp.text

            # -------------------------------------------------------------------
            # Step 7: SKU Detail Screen (GET /skus/{sku_id})
            # -------------------------------------------------------------------
            sku_detail_resp = await client.get(f"/skus/{sku_id}")
            assert sku_detail_resp.status_code == 200
            assert spec_no in sku_detail_resp.text
            assert "MIL-W-4088K_E2E.pdf" in sku_detail_resp.text
            assert "LOADED" in sku_detail_resp.text

            print("\n[PASS] Full End-to-End Spec PDF Ingestion, Review, Verification & Detail UI workflow verified successfully!")
    finally:
        await conn.close()
