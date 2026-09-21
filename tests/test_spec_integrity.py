"""
tests/test_spec_integrity.py — Cryptographic Integrity Verification & Corruption Detection Tests.

CRITICAL NON-NEGOTIABLE TEST SUITE:
1. Tests clean ingestion and verification (returns VALID, pdf_match=True, audit_chain_valid=True)
2. Deliberately flips 1 byte in the stored PDF -> verifies that Python layer computes the altered
   hash and PostgreSQL verify_spec_pdf_integrity() detects pdf_match=False and returns CORRUPTED.
3. Deliberately flips 1 byte in the stored raw JSON -> verifies parsed_json_match=False and CORRUPTED.
4. Deliberately corrupts an audit_log hash-chain entry -> verifies audit_chain_valid=False and CORRUPTED.
5. Verifies database-level auth_can('audit', 'read') security guard refuses unauthorized callers.
"""

import hashlib
import json
import os
import uuid
import pytest
import asyncpg
from services.spec_integrity import verify_upload_integrity, compute_bytes_sha256
from services.spec_loader import load_specification_document
from services.storage import UPLOAD_BASE_DIR, upload_file_to_storage


@pytest.mark.asyncio
async def test_spec_pdf_integrity_clean_and_corruption():
    """
    End-to-end cryptographic verification test:
    1. Clean baseline check -> VALID
    2. Byte-corruption in stored PDF -> CORRUPTED (pdf_match=False)
    3. Byte-corruption in stored JSON -> CORRUPTED (parsed_json_match=False)
    4. Tampering with audit_log table -> CORRUPTED (audit_chain_valid=False)
    """
    conn = await asyncpg.connect("postgresql://postgres@127.0.0.1:5433/snm_test_db")
    try:
        # Ensure chief_quality user exists with role in DB
        chief_quality_id = uuid.UUID("44444444-4444-4444-4444-444444444444")
        await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1, 'chief.quality@snmills.com') ON CONFLICT (id) DO NOTHING;", chief_quality_id)
        await conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1, 'Chief Quality', 'owner', true) ON CONFLICT (id) DO UPDATE SET active = true;", chief_quality_id)
        await conn.execute("INSERT INTO user_roles (user_id, role_code, active) VALUES ($1, 'chief_quality', true) ON CONFLICT (user_id, role_code) DO UPDATE SET active = true;", chief_quality_id)

        # 1. Create a dummy test SKU (as admin before switching role)
        sku_id = uuid.uuid4()
        sku_code = f"TEST-SKU-{uuid.uuid4().hex[:6].upper()}"
        await conn.execute(
            """
            INSERT INTO skus (id, sku_code, family, title, status)
            VALUES ($1, $2, 'Narrow woven', 'Test Webbing SKU', 'Active');
            """,
            sku_id,
            sku_code,
        )

        # Set session-level identity for chief_quality (has specifications:create and audit:read)
        await conn.execute("SET ROLE authenticated;")
        await conn.execute(
            "SELECT set_config('request.jwt.claims', $1, false);",
            json.dumps({"sub": str(chief_quality_id), "role": "authenticated"}),
        )

        # 2. Upload dummy PDF and dummy parsed JSON
        upload_id = uuid.uuid4()
        raw_pdf_bytes = b"%PDF-1.4 Mock Spec PDF for Integrity Verification Testing."
        raw_pdf_sha256 = compute_bytes_sha256(raw_pdf_bytes)

        parsed_json_dict = {
            "specification": {
                "spec_no": f"TEST-SPEC-{uuid.uuid4().hex[:4].upper()}",
                "revision": "A",
                "title": "TEST INTEGRITY SPEC",
            },
            "variants": [{"key": "T1", "designation": "Type I", "class": "1", "sort_order": 1}],
            "requirements": [],
            "defects": [],
            "sampling": [],
        }
        raw_json_bytes = json.dumps(parsed_json_dict).encode("utf-8")
        raw_json_sha256 = compute_bytes_sha256(raw_json_bytes)

        pdf_storage_rel = f"{sku_code}/{upload_id}.pdf"
        await upload_file_to_storage("spec-docs", pdf_storage_rel, raw_pdf_bytes, "application/pdf")

        json_storage_rel = f"{upload_id}_raw.json"
        await upload_file_to_storage("spec-parsed", json_storage_rel, raw_json_bytes, "application/json")

        # 3. Insert record in spec_pdf_uploads
        await conn.execute(
            """
            INSERT INTO spec_pdf_uploads (
                id, sku_id, storage_path, pdf_sha256, parsed_json_path,
                parsed_json_sha256, source, original_filename, file_size_bytes,
                status, uploaded_by
            ) VALUES (
                $1, $2, $3, $4, $5, $6, 'web_upload', 'test_spec.pdf', $7, 'Parsed', $8
            );
            """,
            upload_id,
            sku_id,
            f"spec-docs/{pdf_storage_rel}",
            raw_pdf_sha256,
            f"spec-parsed/{json_storage_rel}",
            raw_json_sha256,
            len(raw_pdf_bytes),
            chief_quality_id,
        )

        # -----------------------------------------------------------------------
        # TEST A: Clean Baseline Verification
        # -----------------------------------------------------------------------
        res_clean = await verify_upload_integrity(conn, upload_id)
        print("\n--- [CASE A: CLEAN BASELINE] ---")
        print(f"Stored PDF SHA-256:   {raw_pdf_sha256}")
        print(f"Stored JSON SHA-256:  {raw_json_sha256}")
        print(f"Returned Row: {json.dumps(res_clean, default=str, indent=2)}")
        assert res_clean["status"] == "VALID", f"Expected VALID but got: {res_clean}"
        assert res_clean["pdf_match"] is True
        assert res_clean["parsed_json_match"] is True
        assert res_clean["audit_chain_valid"] is True

        # -----------------------------------------------------------------------
        # TEST B: Deliberate 1-Byte PDF Corruption Detection
        # -----------------------------------------------------------------------
        corrupted_pdf_bytes = raw_pdf_bytes[:-1] + b"X"  # Alter last byte
        corrupted_pdf_sha256 = compute_bytes_sha256(corrupted_pdf_bytes)
        local_pdf_path = os.path.join(UPLOAD_BASE_DIR, "spec-docs", pdf_storage_rel)
        with open(local_pdf_path, "wb") as f:
            f.write(corrupted_pdf_bytes)

        res_corrupt_pdf = await verify_upload_integrity(conn, upload_id)
        print("\n--- [CASE B: 1-BYTE PDF CORRUPTION] ---")
        print(f"Original PDF SHA-256:  {raw_pdf_sha256}")
        print(f"Corrupted PDF SHA-256: {corrupted_pdf_sha256}")
        print(f"Returned Row: {json.dumps(res_corrupt_pdf, default=str, indent=2)}")
        assert res_corrupt_pdf["pdf_match"] is False, "Corrupted PDF should not match stored hash"
        assert res_corrupt_pdf["status"] == "CORRUPTED", "Status must be CORRUPTED"

        # Restore clean PDF
        with open(local_pdf_path, "wb") as f:
            f.write(raw_pdf_bytes)

        # -----------------------------------------------------------------------
        # TEST C: Deliberate 1-Byte JSON Corruption Detection
        # -----------------------------------------------------------------------
        corrupted_json_bytes = raw_json_bytes[:-1] + b"X"  # Alter last byte
        corrupted_json_sha256 = compute_bytes_sha256(corrupted_json_bytes)
        local_json_path = os.path.join(UPLOAD_BASE_DIR, "spec-parsed", json_storage_rel)
        with open(local_json_path, "wb") as f:
            f.write(corrupted_json_bytes)

        res_corrupt_json = await verify_upload_integrity(conn, upload_id)
        print("\n--- [CASE C: 1-BYTE JSON CORRUPTION] ---")
        print(f"Original JSON SHA-256:  {raw_json_sha256}")
        print(f"Corrupted JSON SHA-256: {corrupted_json_sha256}")
        print(f"Returned Row: {json.dumps(res_corrupt_json, default=str, indent=2)}")
        assert res_corrupt_json["parsed_json_match"] is False, "Corrupted JSON should not match stored hash"
        assert res_corrupt_json["status"] == "CORRUPTED", "Status must be CORRUPTED"

        # Restore clean JSON
        with open(local_json_path, "wb") as f:
            f.write(raw_json_bytes)

        # -----------------------------------------------------------------------
        # TEST D: Deliberate Audit Chain Tampering Detection
        # -----------------------------------------------------------------------
        # Temporarily switch to superuser to tamper with an audit_log hash row
        await conn.execute("SET ROLE postgres;")
        latest_audit = await conn.fetchrow("SELECT id, row_hash FROM audit_log ORDER BY id DESC LIMIT 1;")
        assert latest_audit is not None, "audit_log must contain records from previous inserts"
        audit_id = latest_audit["id"]
        orig_row_hash = latest_audit["row_hash"]

        # Tamper with the row_hash directly in audit_log
        tampered_hash = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
        await conn.execute("UPDATE audit_log SET row_hash = $1 WHERE id = $2;", tampered_hash, audit_id)

        # Switch back to authenticated chief_quality identity
        await conn.execute("SET ROLE authenticated;")
        await conn.execute(
            "SELECT set_config('request.jwt.claims', $1, false);",
            json.dumps({"sub": str(chief_quality_id), "role": "authenticated"}),
        )

        res_corrupt_audit = await verify_upload_integrity(conn, upload_id)
        print("\n--- [CASE D: AUDIT CHAIN TAMPERING] ---")
        print(f"Tampered Audit Row ID {audit_id}: Original={orig_row_hash}, Tampered={tampered_hash}")
        print(f"Returned Row: {json.dumps(res_corrupt_audit, default=str, indent=2)}")
        assert res_corrupt_audit["audit_chain_valid"] is False, "Tampered audit chain must return audit_chain_valid=False"
        assert res_corrupt_audit["status"] == "CORRUPTED", "Overall status must be CORRUPTED when audit chain is broken"

        # Restore audit row hash
        await conn.execute("SET ROLE postgres;")
        await conn.execute("UPDATE audit_log SET row_hash = $1 WHERE id = $2;", orig_row_hash, audit_id)

    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_verify_spec_pdf_integrity_permission_guard():
    """
    Verifies that a user lacking audit:read permission is blocked by the database function
    with the exact exception: 'Not authorized to verify spec audit integrity'.
    """
    conn = await asyncpg.connect("postgresql://postgres@127.0.0.1:5433/snm_test_db")
    try:
        # Operator has no audit:read permission
        operator_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1, 'operator.loom@snmills.com') ON CONFLICT (id) DO NOTHING;", operator_id)
        await conn.execute("INSERT INTO profiles (id, full_name, role, active) VALUES ($1, 'Loom Operator', 'operator', true) ON CONFLICT (id) DO UPDATE SET active = true;", operator_id)
        await conn.execute("INSERT INTO user_roles (user_id, role_code, active) VALUES ($1, 'machine_operator', true) ON CONFLICT (user_id, role_code) DO UPDATE SET active = true;", operator_id)
        await conn.execute("SET ROLE authenticated;")
        await conn.execute(
            "SELECT set_config('request.jwt.claims', $1, false);",
            json.dumps({"sub": str(operator_id), "role": "authenticated"}),
        )

        with pytest.raises(asyncpg.RaiseError, match="Not authorized to verify spec audit integrity"):
            await conn.fetchrow(
                "SELECT * FROM verify_spec_pdf_integrity($1, $2, $3);",
                uuid.uuid4(),
                "dummy_hash",
                "dummy_hash",
            )
        print("\n[PASS] Database permission guard refused unauthorized user with exact exception 'Not authorized to verify spec audit integrity'")
    finally:
        await conn.close()
