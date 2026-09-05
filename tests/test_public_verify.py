"""
tests/test_public_verify.py — Test Suite for Public Test Certificate Verification.

Validates:
1. Unauthenticated public access to /verify/{cert_no}/{hash_fragment} with exact 32-char SHA-256 slice.
2. Fail-closed security behavior:
   - Wrong 32-char hash -> 404
   - Short hash (< 32 chars) -> 404
   - Non-hex characters -> 404
   - Non-existent certificate -> 404
   - Revoked certificate -> 200 with prominent REVOKED banner & reason
3. Commercial & Customer Data Exclusion:
   - Zero leakage of customer name, customer ID, PO reference, price, yarn lot, or supplier.
4. In-memory IP rate limiting (60 requests/min).
"""

import hashlib
import uuid
from datetime import date
import asyncpg
import httpx
import pytest
from starlette.status import (
    HTTP_200_OK,
    HTTP_404_NOT_FOUND,
    HTTP_429_TOO_MANY_REQUESTS,
)

from tests.conftest import LOCAL_TEST_DATABASE_URL, TEST_USERS
from main import app
import routers.public_verify as pv


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Clear rate limiting timestamps between test runs."""
    pv._ip_request_timestamps.clear()


@pytest.mark.asyncio
async def test_public_certificate_verification_valid():
    """
    Test that an unauthenticated user can verify an authentic, issued certificate
    using its exact 32-character SHA-256 hash fragment.
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        secret_buyer = f"TOP SECRET BUYER {uuid.uuid4().hex[:6].upper()}"
        cust_id = await conn.fetchval(
            """
            INSERT INTO customers (id, name, active)
            VALUES (gen_random_uuid(), $1, true)
            RETURNING id;
            """,
            secret_buyer
        )
        job_no = f"JOB-PUB-{uuid.uuid4().hex[:6].upper()}"
        job_id = await conn.fetchval(
            """
            INSERT INTO jobs (
                id, job_no, customer_id, product, status, qty_ordered, unit,
                spec, po_reference, agreed_rate
            ) VALUES (
                gen_random_uuid(), $1, $2, 'MIL-W-4088 Webbing 1.75in', 'Complete', 5000, 'm',
                'MIL-W-4088K Type VIII', 'SECRET-PO-9999', 150.00
            ) RETURNING id;
            """,
            job_no, cust_id
        )

        cert_no = f"TC-2026-PUB{uuid.uuid4().hex[:6].upper()}"
        dummy_pdf = b"%PDF-1.4 Mock certificate content for public verification"
        full_hash = hashlib.sha256(dummy_pdf).hexdigest()
        hash_fragment_32 = full_hash[:32]
        issuer_id = uuid.UUID(TEST_USERS["qa_manager"]["id"])

        cert_id = await conn.fetchval(
            """
            INSERT INTO test_certificates (
                id, cert_no, job_id, status, sha256_hash, issued_by, issued_at
            ) VALUES (
                gen_random_uuid(), $1, $2, 'Issued', $3, $4, now()
            ) RETURNING id;
            """,
            cert_no, job_id, full_hash, issuer_id
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/verify/{cert_no}/{hash_fragment_32}")
            assert resp.status_code == HTTP_200_OK
            content = resp.text

            # Must contain authenticity indicators
            assert "AUTHENTIC CONFORMANCE CERTIFICATE VERIFIED" in content
            assert cert_no in content
            assert "MIL-W-4088K Type VIII" in content
            assert "MIL-W-4088 Webbing 1.75in" in content
            assert hash_fragment_32 in content

            # Critical data protection: MUST NOT leak customer, PO, pricing or internal IDs
            assert secret_buyer not in content
            assert "SECRET-PO-9999" not in content
            assert "150.00" not in content
            assert str(cust_id) not in content
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_public_certificate_verification_fails_closed():
    """
    Test that invalid parameters, short fragments, non-hex strings fail closed (404),
    and revoked certificates render revoked banner with reason (200).
    """
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    try:
        cust_id = await conn.fetchval("SELECT id FROM customers LIMIT 1;")
        job_no = f"JOB-FAIL-{uuid.uuid4().hex[:6].upper()}"
        job_id = await conn.fetchval(
            """
            INSERT INTO jobs (id, job_no, customer_id, product, status, qty_ordered, unit)
            VALUES (gen_random_uuid(), $1, $2, 'Narrow Webbing', 'Complete', 1000, 'm')
            RETURNING id;
            """,
            job_no, cust_id
        )
        issuer_id = uuid.UUID(TEST_USERS["qa_manager"]["id"])

        # 1. Issued Certificate
        cert_no_valid = f"TC-2026-VAL{uuid.uuid4().hex[:6].upper()}"
        full_hash = hashlib.sha256(b"Valid content").hexdigest()
        hash_32 = full_hash[:32]
        await conn.execute(
            """
            INSERT INTO test_certificates (id, cert_no, job_id, status, sha256_hash, issued_by, issued_at)
            VALUES (gen_random_uuid(), $1, $2, 'Issued', $3, $4, now());
            """,
            cert_no_valid, job_id, full_hash, issuer_id
        )

        # 2. Revoked Certificate
        cert_no_revoked = f"TC-2026-REV{uuid.uuid4().hex[:6].upper()}"
        rev_hash = hashlib.sha256(b"Revoked content").hexdigest()
        rev_hash_32 = rev_hash[:32]
        await conn.execute(
            """
            INSERT INTO test_certificates (id, cert_no, job_id, status, sha256_hash, issued_by, issued_at, revoked_by, revoked_at, revocation_reason)
            VALUES (gen_random_uuid(), $1, $2, 'Revoked', $3, $4, now(), $4, now(), 'Defect found during audit');
            """,
            cert_no_revoked, job_id, rev_hash, issuer_id
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # Case A: Wrong 32-char hash
            wrong_hash_32 = "0" * 32
            r1 = await client.get(f"/verify/{cert_no_valid}/{wrong_hash_32}")
            assert r1.status_code == HTTP_404_NOT_FOUND
            assert "not found" in r1.text.lower() or "invalid" in r1.text.lower()

            # Case B: Short hash (< 32 chars, e.g. 8 chars)
            r2 = await client.get(f"/verify/{cert_no_valid}/{hash_32[:8]}")
            assert r2.status_code == HTTP_404_NOT_FOUND

            # Case C: Short hash (16 chars)
            r3 = await client.get(f"/verify/{cert_no_valid}/{hash_32[:16]}")
            assert r3.status_code == HTTP_404_NOT_FOUND

            # Case D: Non-hex characters
            r4 = await client.get(f"/verify/{cert_no_valid}/{'g' * 32}")
            assert r4.status_code == HTTP_404_NOT_FOUND

            # Case E: Revoked certificate renders REVOKED banner with revocation reason
            r5 = await client.get(f"/verify/{cert_no_revoked}/{rev_hash_32}")
            assert r5.status_code == HTTP_200_OK
            assert "CERTIFICATE REVOKED" in r5.text
            assert "Defect found during audit" in r5.text

            # Case F: Non-existent cert number
            r6 = await client.get(f"/verify/TC-9999-DOESNOTEXIST/{hash_32}")
            assert r6.status_code == HTTP_404_NOT_FOUND
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_public_verification_rate_limiting():
    """
    Test that rapid repeated requests from the same client IP trigger HTTP 429 Too Many Requests.
    """
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Exhaust allowed 60 requests
        for i in range(60):
            res = await client.get("/verify/TC-2026-0001/0123456789abcdef0123456789abcdef", headers={"x-forwarded-for": "198.51.100.42"})
            assert res.status_code in (HTTP_200_OK, HTTP_404_NOT_FOUND)

        # 61st request must be rejected with 429
        res_limit = await client.get("/verify/TC-2026-0001/0123456789abcdef0123456789abcdef", headers={"x-forwarded-for": "198.51.100.42"})
        assert res_limit.status_code == HTTP_429_TOO_MANY_REQUESTS
        assert "Too Many Requests" in res_limit.text
