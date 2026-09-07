# -*- coding: utf-8 -*-
from datetime import date, datetime
import logging
from typing import Any, Dict, List, Optional
import uuid
import asyncpg
from starlette.status import (
    HTTP_200_OK,
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_404_NOT_FOUND,
)
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import database
from database import get_db
from auth.dependencies import current_user, require
from auth.jwt import decode_access_token
from auth.middleware import set_rls_claims

logger = logging.getLogger("snm_works.jobs")
router = APIRouter(prefix="/jobs", tags=["Jobs"])
templates = Jinja2Templates(directory="templates")

# Forward-only status state machine
# Planned -> In progress -> Complete -> Despatched (Cancelled is always allowed)
STATUS_STAGES = ["Planned", "In progress", "Complete", "Despatched"]
STATUS_ORDER = {stage: i for i, stage in enumerate(STATUS_STAGES)}

# In-memory storage cache for local fallback
MEM_JOBS: Dict[str, Dict[str, Any]] = {}
MEM_CUSTOMERS: List[Dict[str, Any]] = [
    {"id": "c1000000-0000-0000-0000-000000000001", "name": "Ordnance Factory Kanpur (OFK)"},
    {"id": "c1000000-0000-0000-0000-000000000002", "name": "Heavy Vehicles Factory (HVF)"},
    {"id": "c1000000-0000-0000-0000-000000000003", "name": "Aero Defence Systems Pvt Ltd"},
    {"id": "c1000000-0000-0000-0000-000000000004", "name": "Northern Parachutes & Ropes"},
]


def validate_status_transition(current_status: str, new_status: str) -> tuple[bool, str]:
    """
    Validates status transition:
    - Status can only move forward: Planned -> In progress -> Complete -> Despatched.
    - Cancelled is always allowed from any state.
    - Cannot move away from Cancelled once cancelled.
    """
    clean_curr = (current_status or "Planned").strip()
    clean_new = (new_status or "Planned").strip()

    if clean_curr == clean_new:
        return True, ""

    if clean_new == "Cancelled":
        return True, ""

    if clean_curr == "Cancelled":
        return False, "Job is Cancelled and cannot be reopened."

    curr_rank = STATUS_ORDER.get(clean_curr)
    new_rank = STATUS_ORDER.get(clean_new)

    if curr_rank is None or new_rank is None:
        return False, f"Invalid status: {clean_new}"

    if new_rank < curr_rank:
        return False, f"Status cannot move backward from '{clean_curr}' to '{clean_new}'. Status progression is Planned -> In progress -> Complete -> Despatched."

    return True, ""


async def get_user_claims(request: Request) -> Dict[str, Any]:
    """
    Extracts and validates Supabase JWT token from cookie or Authorization header.
    """
    token = request.cookies.get("access_token")
    if not token:
        token = request.cookies.get("sb-access-token")

    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]

    if not token:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    return decode_access_token(token)


async def get_next_job_no(conn: Optional[asyncpg.Connection] = None) -> str:
    """
    Auto-generates next job_no using exact query:
    select coalesce(
      max(substring(job_no from 'SNM/26-27/(\\d+)')::int),
      0
    ) + 1 as next_seq
    from jobs
    where job_no like 'SNM/26-27/%'
    """
    next_seq = 1
    if conn is not None:
        try:
            seq_val = await conn.fetchval(
                """
                SELECT COALESCE(
                  MAX(SUBSTRING(job_no FROM 'SNM/26-27/(\\d+)')::int),
                  0
                ) + 1 AS next_seq
                FROM jobs
                WHERE job_no LIKE 'SNM/26-27/%'
                """
            )
            if seq_val is not None:
                next_seq = int(seq_val)
        except Exception as exc:
            logger.warning(f"Could not compute next sequence from database: {exc}")
            existing_seqs = []
            for j in MEM_JOBS.values():
                jno = j.get("job_no", "")
                if jno.startswith("SNM/26-27/"):
                    try:
                        part = int(jno.split("SNM/26-27/")[1])
                        existing_seqs.append(part)
                    except ValueError:
                        pass
            if existing_seqs:
                next_seq = max(existing_seqs) + 1
    else:
        existing_seqs = []
        for j in MEM_JOBS.values():
            jno = j.get("job_no", "")
            if jno.startswith("SNM/26-27/"):
                try:
                    part = int(jno.split("SNM/26-27/")[1])
                    existing_seqs.append(part)
                except ValueError:
                    pass
        if existing_seqs:
            next_seq = max(existing_seqs) + 1

    return f"SNM/26-27/{next_seq:04d}"


async def fetch_customers_list(conn: Optional[asyncpg.Connection] = None) -> List[Dict[str, Any]]:
    """
    Fetches active customers for dropdowns.
    """
    if conn is not None:
        try:
            rows = await conn.fetch("SELECT id::text, name FROM customers WHERE active = true ORDER BY name ASC")
            return [dict(r) for r in rows]
        except Exception:
            pass
    return MEM_CUSTOMERS


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def list_jobs(
    request: Request,
    status_filter: Optional[str] = None,
    q: Optional[str] = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("jobs", "read")),
):
    """
    GET /jobs -- List all jobs from the jobs table ordered by raised_on descending.
    """
    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    jobs_list: List[Dict[str, Any]] = []
    today = date.today()

    try:
        query = """
            SELECT 
                j.id::text as id,
                j.job_no,
                j.raised_on,
                j.customer_id::text as customer_id,
                c.name as customer_name,
                j.po_ref,
                j.product,
                j.spec,
                j.width_mm,
                j.colour,
                j.qty_ordered,
                j.unit,
                j.qty_produced,
                (j.qty_ordered - j.qty_produced) as balance,
                j.machine,
                j.delivery_due,
                j.status,
                j.remarks,
                j.created_at,
                j.updated_at,
                COALESCE(qc_fail.fail_count, 0) as qc_fail_count,
                COALESCE(lab_fail.fail_count, 0) as lab_fail_count
            FROM jobs j
            LEFT JOIN customers c ON c.id = j.customer_id
            LEFT JOIN (
                SELECT job_id, COUNT(*) as fail_count 
                FROM qc_checks 
                WHERE verdict = 'FAIL' 
                GROUP BY job_id
            ) qc_fail ON qc_fail.job_id = j.id
            LEFT JOIN (
                SELECT job_id, COUNT(*) as fail_count 
                FROM lab_tests 
                WHERE verdict = 'FAIL' 
                GROUP BY job_id
            ) lab_fail ON lab_fail.job_id = j.id
            WHERE 1=1
        """
        params: List[Any] = []
        idx = 1

        if status_filter and status_filter.strip() and status_filter.lower() != "all":
            query += f" AND j.status = ${idx}"
            params.append(status_filter.strip())
            idx += 1

        if q and q.strip():
            search_term = f"%{q.strip()}%"
            query += f" AND (j.job_no ILIKE ${idx} OR j.product ILIKE ${idx} OR c.name ILIKE ${idx})"
            params.append(search_term)
            idx += 1

        query += " ORDER BY j.raised_on DESC, j.job_no DESC"

        rows = await conn.fetch(query, *params)
        for r in rows:
            item = dict(r)
            item["has_hold"] = (item.get("qc_fail_count", 0) > 0) or (item.get("lab_fail_count", 0) > 0)
            
            deliv = item.get("delivery_due")
            item["is_overdue"] = False
            if deliv:
                if isinstance(deliv, str):
                    try:
                        deliv = datetime.strptime(deliv[:10], "%Y-%m-%d").date()
                    except Exception:
                        deliv = None
                if deliv and deliv < today and item.get("status") not in ("Complete", "Despatched", "Cancelled"):
                    item["is_overdue"] = True

            jobs_list.append(item)
    except Exception as exc:
        logger.warning(f"Could not load jobs from database: {exc}")

    # Fallback / merge in-memory jobs for local development
    for m_id, m_job in MEM_JOBS.items():
        if not any(j.get("id") == m_id or j.get("job_no") == m_job.get("job_no") for j in jobs_list):
            item = dict(m_job)
            item.setdefault("qty_ordered", 0)
            item.setdefault("qty_produced", 0)
            item.setdefault("unit", "m")
            item.setdefault("product", "")
            item.setdefault("customer_name", "")
            item.setdefault("status", "Planned")
            item["balance"] = float(item.get("qty_ordered", 0)) - float(item.get("qty_produced", 0))
            item["has_hold"] = item.get("has_hold", False)
            deliv = item.get("delivery_due")
            item["is_overdue"] = False
            if deliv:
                if isinstance(deliv, str):
                    try:
                        deliv = datetime.strptime(deliv[:10], "%Y-%m-%d").date()
                    except Exception:
                        deliv = None
                if deliv and deliv < today and item.get("status") not in ("Complete", "Despatched", "Cancelled"):
                    item["is_overdue"] = True

            if status_filter and status_filter.strip() and status_filter.lower() != "all":
                if item.get("status") != status_filter.strip():
                    continue
            if q and q.strip():
                q_lower = q.strip().lower()
                if (q_lower not in item.get("job_no", "").lower() and
                    q_lower not in item.get("product", "").lower() and
                    q_lower not in (item.get("customer_name") or "").lower()):
                    continue

            jobs_list.insert(0, item)

    return templates.TemplateResponse(
        request=request,
        name="jobs/list.html",
        context={
            "user": user_info,
            "jobs": jobs_list,
            "status_filter": status_filter or "all",
            "q": q or "",
            "status_stages": STATUS_STAGES,
            "current_page": "jobs",
            "current_func": "OPS",
        }
    )


@router.get("/new", response_class=HTMLResponse)
async def new_job_form(request: Request):
    """
    GET /jobs/new -- render the create form with auto-generated preview job number.
    """
    try:
        claims = await get_user_claims(request)
        user_info = {
            "id": claims.get("sub"),
            "email": claims.get("email"),
            "full_name": claims.get("user_metadata", {}).get("full_name") or claims.get("email"),
        }
    except HTTPException:
        return RedirectResponse(url="/", status_code=HTTP_303_SEE_OTHER)

    preview_job_no = "SNM/26-27/0001"
    customers: List[Dict[str, Any]] = []

    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, claims)
                    preview_job_no = await get_next_job_no(conn)
                    customers = await fetch_customers_list(conn)
        except Exception as exc:
            logger.warning(f"Error loading new job form data: {exc}")
            preview_job_no = await get_next_job_no(None)
            customers = await fetch_customers_list(None)
    else:
        preview_job_no = await get_next_job_no(None)
        customers = await fetch_customers_list(None)

    return templates.TemplateResponse(
        request=request,
        name="jobs/form.html",
        context={
            "user": user_info,
            "is_edit": False,
            "job": {
                "job_no": preview_job_no,
                "status": "Planned",
                "unit": "m",
                "qty_ordered": 1000,
                "qty_produced": 0,
                "raised_on": date.today().isoformat(),
            },
            "customers": customers,
            "status_stages": STATUS_STAGES,
            "current_page": "jobs",
            "current_func": "OPS",
        }
    )


@router.post("", response_class=HTMLResponse)
@router.post("/", response_class=HTMLResponse)
async def create_job(
    request: Request,
    customer_id: Optional[str] = Form(None),
    customer_name: Optional[str] = Form(None),
    po_ref: Optional[str] = Form(None),
    po_reference: Optional[str] = Form(None),
    po_date: Optional[str] = Form(None),
    agreed_rate: Optional[float] = Form(None),
    agreed_qty: Optional[float] = Form(None),
    agreed_unit: Optional[str] = Form(None),
    product: str = Form(...),
    spec: Optional[str] = Form(None),
    width_mm: Optional[float] = Form(None),
    colour: Optional[str] = Form(None),
    qty_ordered: float = Form(...),
    unit: str = Form("m"),
    qty_produced: float = Form(0.0),
    machine: Optional[str] = Form(None),
    delivery_due: Optional[str] = Form(None),
    status: str = Form("Planned"),
    remarks: Optional[str] = Form(None),
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("jobs", "create")),
):
    """
    POST /jobs -- insert a new job. Auto-generate job_no as SNM/26-27/ followed by 4-digit sequence number.
    Redirect to /jobs/{id} on success.
    """
    user_id = user.get("id")

    if qty_ordered <= 0:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Quantity ordered must be greater than 0.")

    clean_product = product.strip()
    clean_po_ref = (po_reference or po_ref or "").strip() or None
    clean_po_reference = (po_reference or po_ref or "").strip() or None
    clean_spec = spec.strip() if spec else None
    clean_colour = colour.strip() if colour else None
    clean_unit = unit.strip() or "m"
    clean_agreed_unit = agreed_unit.strip() if agreed_unit and agreed_unit.strip() else clean_unit
    clean_agreed_rate = agreed_rate if (agreed_rate is not None and agreed_rate >= 0) else None
    clean_agreed_qty = agreed_qty if (agreed_qty is not None and agreed_qty > 0) else qty_ordered
    clean_machine = machine.strip() if machine else None
    clean_status = status.strip() if status else "Planned"
    clean_remarks = remarks.strip() if remarks else None
    parsed_due_date = None
    if delivery_due and delivery_due.strip():
        try:
            parsed_due_date = datetime.strptime(delivery_due.strip(), "%Y-%m-%d").date()
        except ValueError:
            parsed_due_date = None

    parsed_po_date = None
    if po_date and po_date.strip():
        try:
            parsed_po_date = datetime.strptime(po_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            parsed_po_date = None

    new_job_id = str(uuid.uuid4())
    generated_job_no = ""
    resolved_customer_id = None
    resolved_customer_name = customer_name or ""

    if conn is not None:
        try:
            # 1. Generate sequence using exact query
            generated_job_no = await get_next_job_no(conn)

            # 2. Resolve customer
            if customer_id and customer_id.strip():
                try:
                    resolved_customer_id = uuid.UUID(customer_id.strip())
                except Exception:
                    pass
                try:
                    cust_row = await conn.fetchrow("SELECT id, name FROM customers WHERE id::text = $1", customer_id.strip())
                    if cust_row:
                        resolved_customer_name = cust_row["name"]
                except Exception:
                    pass

            if not resolved_customer_id and customer_name and customer_name.strip():
                try:
                    cust_match = await conn.fetchrow("SELECT id, name FROM customers WHERE name ILIKE $1", customer_name.strip())
                    if cust_match:
                        resolved_customer_id = cust_match["id"]
                        resolved_customer_name = cust_match["name"]
                    else:
                        new_c_id = str(uuid.uuid4())
                        await conn.execute(
                            "INSERT INTO customers (id, name, active) VALUES ($1::uuid, $2, true) ON CONFLICT (name) DO NOTHING",
                            new_c_id,
                            customer_name.strip(),
                        )
                        resolved_customer_id = uuid.UUID(new_c_id)
                        resolved_customer_name = customer_name.strip()
                except Exception:
                    # If RLS prevents inline customer creation for role, proceed without blocking job creation
                    pass

            # 3. Insert into jobs table
            await conn.execute(
                """
                INSERT INTO jobs (
                    id, job_no, customer_id, po_ref, po_reference, po_date,
                    agreed_rate, agreed_qty, agreed_unit, product, spec, width_mm,
                    colour, qty_ordered, unit, qty_produced, machine,
                    delivery_due, status, remarks, created_by
                ) VALUES (
                    $1::uuid, $2, $3, $4, $5, $6,
                    $7, $8, $9, $10, $11, $12,
                    $13, $14, $15, $16, $17,
                    $18, $19, $20, $21::uuid
                )
                """,
                new_job_id,
                generated_job_no,
                resolved_customer_id,
                clean_po_ref,
                clean_po_reference,
                parsed_po_date,
                clean_agreed_rate,
                clean_agreed_qty,
                clean_agreed_unit,
                clean_product,
                clean_spec,
                width_mm,
                clean_colour,
                qty_ordered,
                clean_unit,
                qty_produced,
                clean_machine,
                parsed_due_date,
                clean_status,
                clean_remarks,
                user_id,
            )
        except Exception as exc:
            logger.error(f"Error inserting job in database: {exc}")
            if not generated_job_no:
                generated_job_no = await get_next_job_no(None)

    if not generated_job_no:
        generated_job_no = await get_next_job_no(None)

    MEM_JOBS[new_job_id] = {
        "id": new_job_id,
        "job_no": generated_job_no,
        "raised_on": date.today().isoformat(),
        "customer_id": str(resolved_customer_id) if resolved_customer_id else None,
        "customer_name": resolved_customer_name,
        "po_ref": clean_po_ref,
        "product": clean_product,
        "spec": clean_spec,
        "width_mm": width_mm,
        "colour": clean_colour,
        "qty_ordered": qty_ordered,
        "unit": clean_unit,
        "qty_produced": qty_produced,
        "balance": qty_ordered - qty_produced,
        "machine": clean_machine,
        "delivery_due": parsed_due_date.isoformat() if parsed_due_date else None,
        "status": clean_status,
        "remarks": clean_remarks,
        "created_at": datetime.now().isoformat(),
        "has_hold": False,
    }

    is_htmx = request.headers.get("hx-request") == "true"
    if is_htmx:
        response = Response(status_code=HTTP_200_OK)
        response.headers["HX-Redirect"] = f"/jobs/{new_job_id}"
        return response

    return RedirectResponse(url=f"/jobs/{new_job_id}", status_code=HTTP_303_SEE_OTHER)


@router.get("/{job_id}", response_class=HTMLResponse)
async def job_detail_view(request: Request, job_id: str):
    """
    GET /jobs/{job_id} -- detail page showing all fields plus a QC summary section
    showing count of PASS and FAIL checks linked to this job from qc_checks table.
    Shows a red HOLD badge if any linked qc_checks has verdict = 'FAIL' or any linked lab_tests has verdict = 'FAIL'.
    """
    try:
        claims = await get_user_claims(request)
        user_info = {
            "id": claims.get("sub"),
            "email": claims.get("email"),
            "full_name": claims.get("user_metadata", {}).get("full_name") or claims.get("email"),
        }
    except HTTPException:
        return RedirectResponse(url="/", status_code=HTTP_303_SEE_OTHER)

    job_data: Optional[Dict[str, Any]] = None
    qc_checks: List[Dict[str, Any]] = []
    lab_tests: List[Dict[str, Any]] = []
    qc_summary = {"pass_count": 0, "fail_count": 0, "total": 0}
    lab_summary = {"pass_count": 0, "fail_count": 0, "pending_count": 0, "total": 0}
    traceability_data: Dict[str, Any] = {
        "yarn_lots": [],
        "despatches": [],
        "certificates": [],
        "is_material_linked": False,
        "is_despatch_linked": False,
        "is_certificate_linked": False,
    }
    has_hold = False
    today = date.today()

    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, claims)

                    row = await conn.fetchrow(
                        """
                        SELECT 
                            j.id::text as id,
                            j.job_no,
                            j.raised_on,
                            j.customer_id::text as customer_id,
                            c.name as customer_name,
                            c.contact as customer_contact,
                            j.po_ref,
                            j.po_reference,
                            j.po_date,
                            j.agreed_rate,
                            j.agreed_qty,
                            j.agreed_unit,
                            j.product,
                            j.spec,
                            j.width_mm,
                            j.colour,
                            j.qty_ordered,
                            j.unit,
                            j.qty_produced,
                            (j.qty_ordered - j.qty_produced) as balance,
                            j.machine,
                            j.delivery_due,
                            j.status,
                            j.remarks,
                            p.full_name as creator_name,
                            j.created_at,
                            j.updated_at
                        FROM jobs j
                        LEFT JOIN customers c ON c.id = j.customer_id
                        LEFT JOIN profiles p ON p.id = j.created_by
                        WHERE j.id::text = $1 OR j.job_no = $1
                        """,
                        job_id,
                    )
                    if row:
                        job_data = dict(row)

                    if job_data:
                        actual_uuid = job_data["id"]
                        qc_rows = await conn.fetch(
                            """
                            SELECT 
                                id::text as id, check_no, checked_on, stage,
                                parameter, unit, method, limit_type, spec_value,
                                tolerance, actual, defect_code, action_taken, verdict,
                                created_at
                            FROM qc_checks
                            WHERE job_id::text = $1
                            ORDER BY checked_on DESC, created_at DESC
                            """,
                            actual_uuid,
                        )
                        qc_checks = [dict(r) for r in qc_rows]

                        lab_rows = await conn.fetch(
                            """
                            SELECT 
                                id::text as id, test_id, tested_on, test_type,
                                standard, lab, report_no, requirement, result,
                                unit, verdict, remarks, created_at
                            FROM lab_tests
                            WHERE job_id::text = $1
                            ORDER BY tested_on DESC, created_at DESC
                            """,
                            actual_uuid,
                        )
                        lab_tests = [dict(r) for r in lab_rows]

                        # Traceability resolution via PostgreSQL job_traceability()
                        trace_rows = await conn.fetch("SELECT * FROM job_traceability($1::uuid);", actual_uuid)
                        seen_issues = set()
                        seen_despatches = set()
                        seen_certs = set()
                        for tr in trace_rows:
                            if tr["issue_id"] and tr["issue_id"] not in seen_issues:
                                seen_issues.add(tr["issue_id"])
                                traceability_data["yarn_lots"].append({
                                    "issue_id": str(tr["issue_id"]),
                                    "issue_no": tr["issue_no"],
                                    "issue_date": tr["issue_date"],
                                    "qty_issued": float(tr["issue_qty"]) if tr["issue_qty"] is not None else 0,
                                    "unit": tr["issue_unit"] or "kg",
                                    "lot_id": str(tr["yarn_lot_id"]) if tr["yarn_lot_id"] else "",
                                    "lot_no": tr["lot_no"],
                                    "supplier_lot_no": tr["supplier_lot_no"],
                                    "yarn_type": tr["yarn_type"],
                                    "denier": float(tr["denier"]) if tr["denier"] is not None else None,
                                    "filament_count": tr["filament_count"],
                                    "lustre": tr["lustre"],
                                    "colour": tr["colour"],
                                    "lot_qc_status": tr["lot_qc_status"],
                                    "supplier_name": tr["supplier_name"],
                                    "supplier_code": tr["supplier_code"],
                                    "grn_no": tr["grn_no"],
                                    "grn_date": tr["grn_date"],
                                    "po_ref": tr["po_ref"],
                                    "incoming_test_no": tr["incoming_test_no"],
                                    "incoming_test_date": tr["incoming_test_date"],
                                    "incoming_test_verdict": tr["incoming_test_verdict"],
                                })
                            if tr["despatch_id"] and tr["despatch_id"] not in seen_despatches:
                                seen_despatches.add(tr["despatch_id"])
                                traceability_data["despatches"].append({
                                    "id": str(tr["despatch_id"]),
                                    "despatch_no": tr["despatch_no"],
                                    "date": tr["despatch_date"],
                                    "qty": float(tr["despatch_qty"]) if tr["despatch_qty"] is not None else 0,
                                    "unit": tr["despatch_unit"] or "m",
                                    "invoice_no": tr["despatch_invoice_no"],
                                })
                            if tr["certificate_id"] and tr["certificate_id"] not in seen_certs:
                                seen_certs.add(tr["certificate_id"])
                                traceability_data["certificates"].append({
                                    "id": str(tr["certificate_id"]),
                                    "cert_no": tr["certificate_no"],
                                    "issued_at": tr["certificate_issued_at"],
                                    "status": tr["certificate_status"],
                                    "hash": tr["certificate_hash"],
                                })
                        traceability_data["is_material_linked"] = len(traceability_data["yarn_lots"]) > 0
                        traceability_data["is_despatch_linked"] = len(traceability_data["despatches"]) > 0
                        traceability_data["is_certificate_linked"] = len(traceability_data["certificates"]) > 0
        except Exception as exc:
            logger.warning(f"Error fetching job details from DB: {exc}")

    if not job_data:
        job_data = MEM_JOBS.get(job_id)
        if not job_data:
            for j in MEM_JOBS.values():
                if j.get("job_no") == job_id:
                    job_data = j
                    break

    if not job_data:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Job Card not found")

    qty_ord = float(job_data.get("qty_ordered", 0))
    qty_prod = float(job_data.get("qty_produced", 0))
    job_data["balance"] = qty_ord - qty_prod

    deliv = job_data.get("delivery_due")
    job_data["is_overdue"] = False
    if deliv:
        if isinstance(deliv, str):
            try:
                deliv = datetime.strptime(deliv[:10], "%Y-%m-%d").date()
            except Exception:
                deliv = None
        if deliv and deliv < today and job_data.get("status") not in ("Complete", "Despatched", "Cancelled"):
            job_data["is_overdue"] = True

    for qc in qc_checks:
        qc_summary["total"] += 1
        v = (qc.get("verdict") or "").upper()
        if v == "PASS":
            qc_summary["pass_count"] += 1
        elif v == "FAIL":
            qc_summary["fail_count"] += 1
            has_hold = True

    for lt in lab_tests:
        lab_summary["total"] += 1
        v = (lt.get("verdict") or "").upper()
        if v == "PASS":
            lab_summary["pass_count"] += 1
        elif v == "FAIL":
            lab_summary["fail_count"] += 1
            has_hold = True
        else:
            lab_summary["pending_count"] += 1

    job_data["has_hold"] = has_hold or job_data.get("has_hold", False)

    return templates.TemplateResponse(
        request=request,
        name="jobs/detail.html",
        context={
            "user": user_info,
            "job": job_data,
            "qc_checks": qc_checks,
            "lab_tests": lab_tests,
            "issued_materials": issued_materials if 'issued_materials' in locals() else [],
            "traceability": traceability_data,
            "qc_summary": qc_summary,
            "lab_summary": lab_summary,
            "status_stages": STATUS_STAGES,
            "current_page": "jobs",
            "current_func": "OPS",
        }
    )


@router.get("/{job_id}/inspection-plan", response_class=HTMLResponse)
async def get_job_inspection_plan(
    request: Request,
    job_id: str,
    variant_id: Optional[str] = None,
):
    """
    GET /jobs/{job_id}/inspection-plan -- generates dynamic inspection plan
    via PostgreSQL spec_check_plan(variant_id) for the job.
    Requires explicit variant confirmation to prevent fuzzy matching errors.
    """
    try:
        claims = await get_user_claims(request)
        user_info = {
            "id": claims.get("sub"),
            "email": claims.get("email"),
            "full_name": claims.get("user_metadata", {}).get("full_name") or claims.get("email"),
        }
    except HTTPException:
        return RedirectResponse(url="/", status_code=HTTP_303_SEE_OTHER)

    job_data: Optional[Dict[str, Any]] = None
    available_variants: List[Dict[str, Any]] = []
    plan_items: List[Dict[str, Any]] = []
    selected_variant: Optional[Dict[str, Any]] = None

    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, claims)

                    # 1. Fetch Job
                    row = await conn.fetchrow(
                        """
                        SELECT 
                            j.id::text as id,
                            j.job_no,
                            j.raised_on,
                            j.customer_id::text as customer_id,
                            c.name as customer_name,
                            j.po_ref,
                            j.product,
                            j.spec,
                            j.width_mm,
                            j.colour,
                            j.qty_ordered,
                            j.unit,
                            j.qty_produced,
                            j.status,
                            j.remarks
                        FROM jobs j
                        LEFT JOIN customers c ON c.id = j.customer_id
                        WHERE j.id::text = $1 OR j.job_no = $1;
                        """,
                        job_id,
                    )
                    if row:
                        job_data = dict(row)

                    # 2. Fetch Available Specification Variants
                    v_rows = await conn.fetch(
                        """
                        SELECT 
                            v.id::text as id,
                            v.designation,
                            v.designation as variant_code,
                            v.designation as name,
                            v.class,
                            v.description,
                            v.sort_order,
                            v.status,
                            s.spec_no,
                            s.revision,
                            s.title as spec_title,
                            s.issuing_body,
                            s.active as spec_active
                        FROM spec_variants v
                        JOIN specifications s ON s.id = v.spec_id
                        ORDER BY s.spec_no ASC, v.sort_order ASC, v.designation ASC;
                        """
                    )
                    available_variants = [dict(r) for r in v_rows]

                    # 3. If variant_id is provided, call spec_check_plan
                    if variant_id and variant_id.strip():
                        try:
                            var_uuid = uuid.UUID(variant_id.strip())
                            v_row = await conn.fetchrow(
                                """
                                SELECT 
                                    v.id::text as id,
                                    v.designation,
                                    v.designation as variant_code,
                                    v.designation as name,
                                    v.class,
                                    v.description,
                                    v.sort_order,
                                    v.status,
                                    s.spec_no,
                                    s.revision,
                                    s.title as spec_title,
                                    s.issuing_body,
                                    s.active as spec_active
                                FROM spec_variants v
                                JOIN specifications s ON s.id = v.spec_id
                                WHERE v.id = $1;
                                """,
                                var_uuid,
                            )
                            if v_row:
                                selected_variant = dict(v_row)
                                plan_rows = await conn.fetch(
                                    "SELECT * FROM spec_check_plan($1::uuid);",
                                    var_uuid,
                                )
                                plan_items = [dict(r) for r in plan_rows]
                        except ValueError:
                            pass
        except Exception as e:
            logger.error(f"Error fetching job inspection plan: {e}")

    if not job_data:
        raise HTTPException(HTTP_404_NOT_FOUND, f"Job '{job_id}' not found")

    return templates.TemplateResponse(
        request=request,
        name="jobs/inspection_plan.html",
        context={
            "user": user_info,
            "job": job_data,
            "available_variants": available_variants,
            "selected_variant_id": variant_id or "",
            "variant": selected_variant,
            "plan_items": plan_items,
            "current_page": "jobs",
            "current_func": "OPS",
        },
    )


@router.get("/{job_id}/edit", response_class=HTMLResponse)
async def edit_job_form(request: Request, job_id: str):
    """
    GET /jobs/{job_id}/edit -- render edit form populated with current job values.
    """
    try:
        claims = await get_user_claims(request)
        user_info = {
            "id": claims.get("sub"),
            "email": claims.get("email"),
            "full_name": claims.get("user_metadata", {}).get("full_name") or claims.get("email"),
        }
    except HTTPException:
        return RedirectResponse(url="/", status_code=HTTP_303_SEE_OTHER)

    job_data: Optional[Dict[str, Any]] = None
    customers: List[Dict[str, Any]] = []

    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, claims)
                    row = await conn.fetchrow(
                        """
                        SELECT 
                            j.id::text as id, j.job_no, j.raised_on, j.customer_id::text as customer_id,
                            c.name as customer_name, j.po_ref, j.po_reference, j.po_date, j.agreed_rate, j.agreed_qty, j.agreed_unit,
                            j.product, j.spec, j.width_mm, j.colour, j.qty_ordered, j.unit, j.qty_produced, j.machine,
                            j.delivery_due, j.status, j.remarks
                        FROM jobs j
                        LEFT JOIN customers c ON c.id = j.customer_id
                        WHERE j.id::text = $1 OR j.job_no = $1
                        """,
                        job_id,
                    )
                    if row:
                        job_data = dict(row)
                    customers = await fetch_customers_list(conn)
        except Exception as exc:
            logger.warning(f"Error fetching job for edit: {exc}")
            customers = await fetch_customers_list(None)
    else:
        customers = await fetch_customers_list(None)

    if not job_data:
        job_data = MEM_JOBS.get(job_id)
        if not job_data:
            for j in MEM_JOBS.values():
                if j.get("job_no") == job_id:
                    job_data = j
                    break

    if not job_data:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Job not found")

    return templates.TemplateResponse(
        request=request,
        name="jobs/form.html",
        context={
            "user": user_info,
            "is_edit": True,
            "job": job_data,
            "customers": customers,
            "status_stages": STATUS_STAGES,
            "current_page": "jobs",
            "current_func": "OPS",
        }
    )


@router.post("/{job_id}/update", response_class=HTMLResponse)
async def update_job(
    request: Request,
    job_id: str,
    customer_id: Optional[str] = Form(None),
    customer_name: Optional[str] = Form(None),
    po_ref: Optional[str] = Form(None),
    po_reference: Optional[str] = Form(None),
    po_date: Optional[str] = Form(None),
    agreed_rate: Optional[float] = Form(None),
    agreed_qty: Optional[float] = Form(None),
    agreed_unit: Optional[str] = Form(None),
    product: str = Form(...),
    spec: Optional[str] = Form(None),
    width_mm: Optional[float] = Form(None),
    colour: Optional[str] = Form(None),
    qty_ordered: float = Form(...),
    unit: str = Form("m"),
    qty_produced: float = Form(0.0),
    machine: Optional[str] = Form(None),
    delivery_due: Optional[str] = Form(None),
    status: str = Form("Planned"),
    remarks: Optional[str] = Form(None),
):
    """
    POST /jobs/{job_id}/update -- update job fields.
    Status can only move forward: Planned -> In progress -> Complete -> Despatched.
    Cancelled is always allowed. Never delete.
    """
    try:
        claims = await get_user_claims(request)
    except HTTPException:
        return RedirectResponse(url="/", status_code=HTTP_303_SEE_OTHER)

    if qty_ordered <= 0:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Quantity ordered must be greater than 0.")

    clean_product = product.strip()
    clean_po_ref = (po_reference or po_ref or "").strip() or None
    clean_po_reference = (po_reference or po_ref or "").strip() or None
    clean_spec = spec.strip() if spec else None
    clean_colour = colour.strip() if colour else None
    clean_unit = unit.strip() or "m"
    clean_agreed_unit = agreed_unit.strip() if agreed_unit and agreed_unit.strip() else clean_unit
    clean_agreed_rate = agreed_rate if (agreed_rate is not None and agreed_rate >= 0) else None
    clean_agreed_qty = agreed_qty if (agreed_qty is not None and agreed_qty > 0) else qty_ordered
    clean_machine = machine.strip() if machine else None
    new_status = status.strip() if status else "Planned"
    clean_remarks = remarks.strip() if remarks else None
    parsed_due_date = None
    if delivery_due and delivery_due.strip():
        try:
            parsed_due_date = datetime.strptime(delivery_due.strip(), "%Y-%m-%d").date()
        except ValueError:
            parsed_due_date = None

    parsed_po_date = None
    if po_date and po_date.strip():
        try:
            parsed_po_date = datetime.strptime(po_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            parsed_po_date = None

    current_status = "Planned"
    actual_uuid = job_id
    resolved_customer_id = None
    resolved_customer_name = customer_name or ""

    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, claims)
                    curr_row = await conn.fetchrow(
                        "SELECT id::text, status, customer_id::text FROM jobs WHERE id::text = $1 OR job_no = $1",
                        job_id,
                    )
                    if curr_row:
                        actual_uuid = curr_row["id"]
                        current_status = curr_row["status"]

                    is_valid, err_msg = validate_status_transition(current_status, new_status)
                    if not is_valid:
                        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=err_msg)

                    if customer_id and customer_id.strip():
                        try:
                            cust_row = await conn.fetchrow("SELECT id, name FROM customers WHERE id::text = $1", customer_id.strip())
                            if cust_row:
                                resolved_customer_id = cust_row["id"]
                                resolved_customer_name = cust_row["name"]
                        except Exception:
                            pass

                    if not resolved_customer_id and customer_name and customer_name.strip():
                        cust_match = await conn.fetchrow("SELECT id, name FROM customers WHERE name ILIKE $1", customer_name.strip())
                        if cust_match:
                            resolved_customer_id = cust_match["id"]
                            resolved_customer_name = cust_match["name"]

                    await conn.execute(
                        """
                        UPDATE jobs
                        SET customer_id = $1,
                            po_ref = $2,
                            po_reference = $3,
                            po_date = $4,
                            agreed_rate = $5,
                            agreed_qty = $6,
                            agreed_unit = $7,
                            product = $8,
                            spec = $9,
                            width_mm = $10,
                            colour = $11,
                            qty_ordered = $12,
                            unit = $13,
                            qty_produced = $14,
                            machine = $15,
                            delivery_due = $16,
                            status = $17,
                            remarks = $18,
                            updated_at = now()
                        WHERE id::text = $19
                        """,
                        resolved_customer_id,
                        clean_po_ref,
                        clean_po_reference,
                        parsed_po_date,
                        clean_agreed_rate,
                        clean_agreed_qty,
                        clean_agreed_unit,
                        clean_product,
                        clean_spec,
                        width_mm,
                        clean_colour,
                        qty_ordered,
                        clean_unit,
                        qty_produced,
                        clean_machine,
                        parsed_due_date,
                        new_status,
                        clean_remarks,
                        actual_uuid,
                    )
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error updating job in DB: {exc}")

    if actual_uuid in MEM_JOBS or job_id in MEM_JOBS:
        target_id = actual_uuid if actual_uuid in MEM_JOBS else job_id
        is_valid, err_msg = validate_status_transition(MEM_JOBS[target_id].get("status", "Planned"), new_status)
        if not is_valid:
            raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=err_msg)
        MEM_JOBS[target_id].update({
            "customer_id": str(resolved_customer_id) if resolved_customer_id else None,
            "customer_name": resolved_customer_name,
            "po_ref": clean_po_ref,
            "product": clean_product,
            "spec": clean_spec,
            "width_mm": width_mm,
            "colour": clean_colour,
            "qty_ordered": qty_ordered,
            "unit": clean_unit,
            "qty_produced": qty_produced,
            "balance": qty_ordered - qty_produced,
            "machine": clean_machine,
            "delivery_due": parsed_due_date.isoformat() if parsed_due_date else None,
            "status": new_status,
            "remarks": clean_remarks,
            "updated_at": datetime.now().isoformat(),
        })

    is_htmx = request.headers.get("hx-request") == "true"
    if is_htmx:
        response = Response(status_code=HTTP_200_OK)
        response.headers["HX-Redirect"] = f"/jobs/{actual_uuid}"
        return response

    return RedirectResponse(url=f"/jobs/{actual_uuid}", status_code=HTTP_303_SEE_OTHER)


@router.post("/{job_id}/status", response_class=HTMLResponse)
async def update_job_status_inline(
    request: Request,
    job_id: str,
    status: str = Form(...),
):
    """
    HTMX endpoint for inline status update directly from table rows or detail view.
    Validates forward-only state machine. Returns updated badge / selector.
    """
    try:
        claims = await get_user_claims(request)
    except HTTPException:
        return Response(content="Unauthorized", status_code=HTTP_401_UNAUTHORIZED)

    new_status = status.strip()
    current_status = "Planned"
    actual_uuid = job_id

    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, claims)
                    curr_row = await conn.fetchrow(
                        "SELECT id::text, status FROM jobs WHERE id::text = $1 OR job_no = $1",
                        job_id,
                    )
                    if curr_row:
                        actual_uuid = curr_row["id"]
                        current_status = curr_row["status"]

                    is_valid, err_msg = validate_status_transition(current_status, new_status)
                    if not is_valid:
                        return HTMLResponse(
                            f'<div class="badge badge-fail" title="{err_msg}">{current_status} (Err)</div>',
                            status_code=HTTP_400_BAD_REQUEST,
                        )

                    await conn.execute(
                        "UPDATE jobs SET status = $1, updated_at = now() WHERE id::text = $2",
                        new_status,
                        actual_uuid,
                    )
                    current_status = new_status
        except Exception as exc:
            logger.warning(f"Error in inline status update: {exc}")

    if actual_uuid in MEM_JOBS:
        curr_status = MEM_JOBS[actual_uuid].get("status", "Planned")
        is_valid, _ = validate_status_transition(curr_status, new_status)
        if is_valid:
            MEM_JOBS[actual_uuid]["status"] = new_status
            current_status = new_status

    badge_class = "badge-gray"
    if current_status == "Planned":
        badge_class = "badge-gray"
    elif current_status == "In progress":
        badge_class = "badge-olive"
    elif current_status == "Complete":
        badge_class = "badge-pass"
    elif current_status == "Despatched":
        badge_class = "badge-pass"
    elif current_status == "Cancelled":
        badge_class = "badge-gray"

    return templates.TemplateResponse(
        request=request,
        name="jobs/_status_badge.html",
        context={
            "job_id": actual_uuid,
            "status": current_status,
            "badge_class": badge_class,
            "status_stages": STATUS_STAGES,
        }
    )
