from datetime import date, datetime
import logging
import math
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from auth.dependencies import current_user, require
from auth.jwt import decode_access_token
from auth.middleware import set_rls_claims
import database
from services.materials_service import issue_job_material
from services.tally_gateway import sync_purchase_voucher_for_grn

logger = logging.getLogger("snm.materials")

router = APIRouter(tags=["materials"])
templates = Jinja2Templates(directory="templates")


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
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    return decode_access_token(token)


@router.get("/materials", response_class=HTMLResponse)
async def materials_dashboard(
    request: Request,
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = Query(None),
    tab: str = Query("lots"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """
    GET /materials -- Materials & Inventory Dashboard.
    Shows inventory metrics, yarn lots register (paginated), recent GRNs, and material issues.
    """
    try:
        claims = await get_user_claims(request)
        user_info = {
            "id": claims.get("sub"),
            "email": claims.get("email"),
            "full_name": claims.get("user_metadata", {}).get("full_name") or claims.get("email"),
        }
    except HTTPException:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    metrics = {
        "total_stock_kg": 0.0,
        "approved_stock_kg": 0.0,
        "quarantine_stock_kg": 0.0,
        "rejected_stock_kg": 0.0,
        "total_lots_count": 0,
        "active_grn_count": 0,
    }
    yarn_lots: List[Dict[str, Any]] = []
    grn_list: List[Dict[str, Any]] = []
    issues_list: List[Dict[str, Any]] = []
    total_count = 0
    total_pages = 1

    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, claims)

                    # 1. Metrics
                    m_row = await conn.fetchrow(
                        """
                        SELECT 
                            COALESCE(SUM(qty_remaining), 0)::float as total_stock_kg,
                            COALESCE(SUM(CASE WHEN qc_status = 'Approved' THEN qty_remaining ELSE 0 END), 0)::float as approved_stock_kg,
                            COALESCE(SUM(CASE WHEN qc_status = 'Quarantine' THEN qty_remaining ELSE 0 END), 0)::float as quarantine_stock_kg,
                            COALESCE(SUM(CASE WHEN qc_status = 'Rejected' THEN qty_remaining ELSE 0 END), 0)::float as rejected_stock_kg,
                            COUNT(*)::int as total_lots_count
                        FROM yarn_lots;
                        """
                    )
                    if m_row:
                        metrics.update(dict(m_row))

                    grn_cnt = await conn.fetchval("SELECT COUNT(*)::int FROM grn;")
                    metrics["active_grn_count"] = grn_cnt or 0

                    # 2. Yarn Lots Register
                    query = """
                        SELECT 
                            y.id::text as id,
                            y.lot_no,
                            y.supplier_lot_no,
                            y.supplier_name,
                            y.yarn_type,
                            y.denier,
                            y.filament_count,
                            y.lustre,
                            y.colour,
                            y.qty_received,
                            y.qty_issued,
                            y.qty_remaining,
                            y.unit,
                            y.qc_status,
                            y.storage_location,
                            y.received_date,
                            y.tested_by::text as tested_by,
                            y.released_by::text as released_by,
                            y.created_by::text as created_by,
                            g.grn_no,
                            COUNT(*) OVER() AS total_count
                        FROM yarn_lots y
                        LEFT JOIN grn g ON g.id = y.grn_id
                        WHERE 1=1
                    """
                    params = []
                    idx = 1
                    if status_filter and status_filter.strip() and status_filter != "all":
                        query += f" AND y.qc_status = ${idx}"
                        params.append(status_filter.strip())
                        idx += 1
                    if search and search.strip():
                        s = f"%{search.strip()}%"
                        query += f" AND (y.lot_no ILIKE ${idx} OR y.supplier_name ILIKE ${idx} OR y.yarn_type ILIKE ${idx} OR y.supplier_lot_no ILIKE ${idx})"
                        params.append(s)
                        idx += 1

                    offset = (page - 1) * page_size
                    query += f" ORDER BY y.created_at DESC LIMIT ${idx} OFFSET ${idx + 1};"
                    params.extend([page_size, offset])

                    lot_rows = await conn.fetch(query, *params)
                    yarn_lots = [dict(r) for r in lot_rows]
                    if lot_rows:
                        total_count = int(lot_rows[0]["total_count"])
                        total_pages = max(1, math.ceil(total_count / page_size))

                    # 3. GRN Register
                    g_rows = await conn.fetch(
                        """
                        SELECT 
                            g.id::text as id,
                            g.grn_no,
                            g.received_date,
                            g.po_ref,
                            g.supplier_name,
                            g.carrier_vehicle,
                            g.invoice_no,
                            g.remarks,
                            COUNT(y.id)::int as lots_count,
                            COALESCE(SUM(y.qty_received), 0)::float as total_qty_received
                        FROM grn g
                        LEFT JOIN yarn_lots y ON y.grn_id = g.id
                        GROUP BY g.id, g.grn_no, g.received_date, g.po_ref, g.supplier_name, g.carrier_vehicle, g.invoice_no, g.remarks
                        ORDER BY g.received_date DESC, g.created_at DESC;
                        """
                    )
                    grn_list = [dict(r) for r in g_rows]

                    # 4. Material Issues
                    iss_rows = await conn.fetch(
                        """
                        SELECT 
                            i.id::text as id,
                            i.issue_no,
                            i.issued_date,
                            i.qty_issued,
                            i.unit,
                            i.remarks,
                            j.job_no,
                            j.product as job_product,
                            y.lot_no,
                            y.yarn_type,
                            y.denier,
                            p.full_name as issued_by_email
                        FROM job_material_issues i
                        JOIN jobs j ON j.id = i.job_id
                        JOIN yarn_lots y ON y.id = i.yarn_lot_id
                        LEFT JOIN profiles p ON p.id = i.issued_by
                        ORDER BY i.issued_date DESC, i.created_at DESC;
                        """
                    )
                    issues_list = [dict(r) for r in iss_rows]

        except Exception as e:
            logger.error(f"Error loading materials dashboard: {e}")

    return templates.TemplateResponse(
        request=request,
        name="materials/index.html",
        context={
            "user": user_info,
            "metrics": metrics,
            "yarn_lots": yarn_lots,
            "grn_list": grn_list,
            "issues_list": issues_list,
            "status_filter": status_filter or "all",
            "search": search or "",
            "tab": tab,
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": total_pages,
            "current_page": "materials",
            "current_func": "SCM",
        },
    )


@router.get("/materials/grn/new", response_class=HTMLResponse)
async def new_grn_form(request: Request):
    """
    GET /materials/grn/new -- Render multi-item Goods Receipt Note entry form.
    """
    try:
        claims = await get_user_claims(request)
        user_info = {
            "id": claims.get("sub"),
            "email": claims.get("email"),
            "full_name": claims.get("user_metadata", {}).get("full_name") or claims.get("email"),
        }
    except HTTPException:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    suppliers: List[Dict[str, Any]] = []
    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, claims)
                    s_rows = await conn.fetch("SELECT id::text, supplier_code, name FROM suppliers WHERE active = true ORDER BY name ASC;")
                    suppliers = [dict(r) for r in s_rows]
        except Exception as e:
            logger.error(f"Error fetching suppliers for GRN: {e}")

    return templates.TemplateResponse(
        request=request,
        name="materials/grn_new.html",
        context={
            "user": user_info,
            "suppliers": suppliers,
            "today": date.today().isoformat(),
            "current_page": "materials",
            "current_func": "SCM",
        },
    )


@router.post("/materials/grn", response_class=RedirectResponse)
async def create_grn(
    request: Request,
    received_date: str = Form(...),
    supplier_name: str = Form(...),
    po_ref: Optional[str] = Form(None),
    supplier_id: Optional[str] = Form(None),
    carrier_vehicle: Optional[str] = Form(None),
    invoice_no: Optional[str] = Form(None),
    invoice_date: Optional[str] = Form(None),
    remarks: Optional[str] = Form(None),
    # Multi-lot arrays
    yarn_type: List[str] = Form(...),
    denier: List[float] = Form(...),
    filament_count: Optional[List[int]] = Form(None),
    lustre: Optional[List[str]] = Form(None),
    colour: Optional[List[str]] = Form(None),
    supplier_lot_no: Optional[List[str]] = Form(None),
    qty_received: List[float] = Form(...),
    unit: List[str] = Form(...),
    storage_location: Optional[List[str]] = Form(None),
    conn=Depends(database.get_db),
    user=Depends(require("stock", "create")),
):
    """
    POST /materials/grn -- Atomically creates 1 GRN receiving header and N linked yarn_lots.
    Restricted to stock.create (store_keeper, chief_supply_chain) or purchase.create.
    """
    uid = user.get("id") or user.get("sub") if isinstance(user, dict) else getattr(user, "id", None)
    user_uuid = uuid.UUID(str(uid)) if uid else None

    sup_uuid = None
    if supplier_id and supplier_id.strip():
        try:
            sup_uuid = uuid.UUID(supplier_id.strip())
        except ValueError:
            pass

    # 1. Generate sequence-based GRN number: GRN-YYYY-NNNN
    current_year = date.today().year
    seq_val = await conn.fetchval("SELECT nextval('grn_seq');")
    grn_no = f"GRN-{current_year}-{seq_val:04d}"

    rec_date_val = date.fromisoformat(received_date) if received_date else date.today()
    inv_date_val = date.fromisoformat(invoice_date) if invoice_date and invoice_date.strip() else None

    # 2. Insert GRN header
    grn_id = await conn.fetchval(
        """
        INSERT INTO grn (
            grn_no, received_date, po_ref, supplier_id, supplier_name,
            carrier_vehicle, invoice_no, invoice_date, remarks, received_by, created_by
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $10)
        RETURNING id;
        """,
        grn_no,
        rec_date_val,
        po_ref.strip() if po_ref else None,
        sup_uuid,
        supplier_name.strip(),
        carrier_vehicle.strip() if carrier_vehicle else None,
        invoice_no.strip() if invoice_no else None,
        inv_date_val,
        remarks.strip() if remarks else None,
        user_uuid,
    )

    # 3. Insert 1:Many Yarn Lots under this GRN
    for i in range(len(yarn_type)):
        lot_seq = await conn.fetchval("SELECT nextval('yarn_lot_seq');")
        lot_no = f"LOT-{current_year}-{lot_seq:04d}"

        y_type = yarn_type[i].strip()
        y_den = float(denier[i])
        y_fil = int(filament_count[i]) if filament_count and i < len(filament_count) and filament_count[i] is not None else None
        y_lus = lustre[i].strip() if lustre and i < len(lustre) and lustre[i] else "Semi-Dull"
        y_col = colour[i].strip() if colour and i < len(colour) and colour[i] else "Raw White / Ecru"
        y_sup_lot = supplier_lot_no[i].strip() if supplier_lot_no and i < len(supplier_lot_no) and supplier_lot_no[i] else None
        y_qty = float(qty_received[i])
        y_unit = unit[i].strip() if unit and i < len(unit) and unit[i] else "kg"
        y_loc = storage_location[i].strip() if storage_location and i < len(storage_location) and storage_location[i] else None

        await conn.execute(
            """
            INSERT INTO yarn_lots (
                lot_no, supplier_lot_no, grn_id, supplier_id, supplier_name,
                yarn_type, denier, filament_count, lustre, colour,
                qty_received, qty_issued, unit, qc_status, storage_location,
                received_date, created_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 0, $12, 'Quarantine', $13, $14, $15);
            """,
            lot_no,
            y_sup_lot,
            grn_id,
            sup_uuid,
            supplier_name.strip(),
            y_type,
            y_den,
            y_fil,
            y_lus,
            y_col,
            y_qty,
            y_unit,
            y_loc,
            rec_date_val,
            user_uuid,
        )

    # 4. Trigger automated Tally Purchase Voucher generation & sync logging
    try:
        await sync_purchase_voucher_for_grn(conn, grn_id, user_uuid)
    except Exception as sync_err:
        logger.warning(f"Tally purchase sync hook error: {sync_err}")

    return RedirectResponse(url="/materials", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/materials/yarn-lots/{lot_id}", response_class=HTMLResponse)
async def get_yarn_lot_detail(request: Request, lot_id: str):
    """
    GET /materials/yarn-lots/{lot_id} -- Yarn lot detail, incoming tests, QA release, and issue history.
    """
    try:
        claims = await get_user_claims(request)
        user_info = {
            "id": claims.get("sub"),
            "email": claims.get("email"),
            "full_name": claims.get("user_metadata", {}).get("full_name") or claims.get("email"),
        }
    except HTTPException:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    lot: Optional[Dict[str, Any]] = None
    lab_tests: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    can_release = False

    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, claims)

                    # 1. Fetch Yarn Lot
                    row = await conn.fetchrow(
                        """
                        SELECT 
                            y.id::text as id,
                            y.lot_no,
                            y.supplier_lot_no,
                            y.supplier_name,
                            y.yarn_type,
                            y.denier,
                            y.filament_count,
                            y.lustre,
                            y.colour,
                            y.qty_received,
                            y.qty_issued,
                            y.qty_remaining,
                            y.unit,
                            y.qc_status,
                            y.storage_location,
                            y.received_date,
                            y.tested_by::text as tested_by,
                            y.released_by::text as released_by,
                            y.released_at,
                            y.created_by::text as created_by,
                            g.grn_no,
                            g.po_ref,
                            g.invoice_no,
                            g.carrier_vehicle
                        FROM yarn_lots y
                        LEFT JOIN grn g ON g.id = y.grn_id
                        WHERE y.id::text = $1 OR y.lot_no = $1;
                        """,
                        lot_id,
                    )
                    if row:
                        lot = dict(row)

                        # 2. Fetch linked incoming lab tests
                        lt_rows = await conn.fetch(
                            """
                            SELECT 
                                id::text as id,
                                test_id,
                                tested_on,
                                test_type,
                                parameter,
                                limit_type,
                                spec_value,
                                tolerance,
                                upper_limit,
                                specimens,
                                verdict,
                                report_no,
                                standard,
                                is_critical,
                                approved_by::text as approved_by,
                                approved_at
                            FROM lab_tests
                            WHERE yarn_lot_id::text = $1
                            ORDER BY tested_on DESC, created_at DESC;
                            """,
                            lot["id"],
                        )
                        lab_tests = [dict(r) for r in lt_rows]

                        # Check permission to release
                        can_approve_test = await conn.fetchval("SELECT auth_can('tests', 'approve');")
                        uid = user_info.get("id")
                        has_pass_test = any(t.get("verdict") == "PASS" for t in lab_tests)
                        # 4-eyes check + incoming test prerequisite:
                        # Cannot release if no test performed, or if user created/tested the lot, or if no test passed
                        if (
                            can_approve_test
                            and lot.get("tested_by")
                            and uid != lot.get("tested_by")
                            and uid != lot.get("created_by")
                            and has_pass_test
                        ):
                            can_release = True

                        # 3. Fetch issue history
                        iss_rows = await conn.fetch(
                            """
                            SELECT 
                                i.id::text as id,
                                i.issue_no,
                                i.issued_date,
                                i.qty_issued,
                                i.unit,
                                i.remarks,
                                j.id::text as job_id,
                                j.job_no,
                                j.product as job_product,
                                p.full_name as issued_by_email
                            FROM job_material_issues i
                            JOIN jobs j ON j.id = i.job_id
                            LEFT JOIN profiles p ON p.id = i.issued_by
                            WHERE i.yarn_lot_id::text = $1
                            ORDER BY i.issued_date DESC, i.created_at DESC;
                            """,
                            lot["id"],
                        )
                        issues = [dict(r) for r in iss_rows]

        except Exception as e:
            logger.error(f"Error fetching yarn lot detail: {e}")

    if not lot:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Yarn lot '{lot_id}' not found")

    return templates.TemplateResponse(
        request=request,
        name="materials/yarn_lot_detail.html",
        context={
            "user": user_info,
            "lot": lot,
            "lab_tests": lab_tests,
            "issues": issues,
            "can_release": can_release,
            "current_page": "materials",
            "current_func": "SCM",
        },
    )


@router.post("/materials/yarn-lots/{lot_id}/release", response_class=RedirectResponse)
async def release_yarn_lot(
    request: Request,
    lot_id: str,
    qc_status: str = Form(...),
    remarks: Optional[str] = Form(None),
    conn=Depends(database.get_db),
    user=Depends(require("tests", "approve")),
):
    """
    POST /materials/yarn-lots/{lot_id}/release -- QA Release of yarn lot (Quarantine -> Approved / Rejected).
    Enforces Segregation of Duties and Quality Gate:
    - tested_by MUST be present before releasing to 'Approved'.
    - At least one linked lab_test with verdict = 'PASS' is strictly required.
    - Creator or Analyst who tested the lot CANNOT release it (4-Eyes principle).
    """
    try:
        lot_uuid = uuid.UUID(lot_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invalid lot ID")

    lot = await conn.fetchrow("SELECT * FROM yarn_lots WHERE id = $1;", lot_uuid)
    if not lot:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Yarn lot not found")

    uid = user.get("id") or user.get("sub") if isinstance(user, dict) else getattr(user, "id", None)
    user_uuid = uuid.UUID(str(uid)) if uid else None

    if qc_status not in ("Approved", "Rejected", "Quarantine"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid QC status '{qc_status}'")

    # Strict Quality Gating for Approval
    if qc_status == "Approved":
        if not lot["tested_by"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot approve a yarn lot with no incoming lab test recorded. At least one incoming test with a PASS verdict is required before QA release.",
            )

        # 4-Eyes Segregation of Duties checks
        if lot["tested_by"] == user_uuid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot release a yarn lot you tested (Segregation of Duties / 4-Eyes principle).",
            )
        if lot["created_by"] and lot["created_by"] == user_uuid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot release a yarn lot you created (Segregation of Duties / 4-Eyes principle).",
            )

        # Check for passed lab test
        pass_cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM lab_tests WHERE yarn_lot_id = $1 AND verdict = 'PASS';",
            lot_uuid,
        )
        if not pass_cnt or pass_cnt == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot approve a yarn lot without at least one passed (PASS verdict) incoming lab test report.",
            )

    try:
        await conn.execute(
            """
            UPDATE yarn_lots
            SET qc_status = $1,
                released_by = $2,
                released_at = now(),
                updated_at = now()
            WHERE id = $3;
            """,
            qc_status,
            user_uuid,
            lot_uuid,
        )
    except Exception as e:
        logger.error(f"QA release failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e).replace('RAISE EXCEPTION', '').strip(),
        )

    return RedirectResponse(url=f"/materials/yarn-lots/{lot_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/materials/issue/new", response_class=HTMLResponse)
async def new_material_issue_form(
    request: Request,
    job_id: Optional[str] = Query(None),
    lot_id: Optional[str] = Query(None),
):
    """
    GET /materials/issue/new -- Form to issue yarn lot stock against a production Job Card.
    """
    try:
        claims = await get_user_claims(request)
        user_info = {
            "id": claims.get("sub"),
            "email": claims.get("email"),
            "full_name": claims.get("user_metadata", {}).get("full_name") or claims.get("email"),
        }
    except HTTPException:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    jobs: List[Dict[str, Any]] = []
    approved_lots: List[Dict[str, Any]] = []

    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, claims)

                    # Active Jobs
                    j_rows = await conn.fetch(
                        "SELECT id::text, job_no, product, status, qty_ordered, unit FROM jobs WHERE status NOT IN ('completed', 'cancelled') ORDER BY job_no DESC;"
                    )
                    jobs = [dict(r) for r in j_rows]

                    # Only Approved Yarn Lots with positive remaining balance
                    l_rows = await conn.fetch(
                        """
                        SELECT 
                            id::text,
                            lot_no,
                            supplier_lot_no,
                            supplier_name,
                            yarn_type,
                            denier,
                            filament_count,
                            qty_remaining,
                            unit,
                            storage_location
                        FROM yarn_lots
                        WHERE qc_status = 'Approved' AND qty_remaining > 0
                        ORDER BY lot_no ASC;
                        """
                    )
                    approved_lots = [dict(r) for r in l_rows]

        except Exception as e:
            logger.error(f"Error fetching issue form data: {e}")

    return templates.TemplateResponse(
        request=request,
        name="materials/issue_new.html",
        context={
            "user": user_info,
            "jobs": jobs,
            "approved_lots": approved_lots,
            "selected_job_id": job_id or "",
            "selected_lot_id": lot_id or "",
            "today": date.today().isoformat(),
            "current_page": "materials",
            "current_func": "SCM",
        },
    )


@router.post("/materials/issue", response_class=RedirectResponse)
async def create_material_issue(
    request: Request,
    job_id: str = Form(...),
    yarn_lot_id: str = Form(...),
    qty_issued: float = Form(...),
    unit: str = Form("kg"),
    issued_date: str = Form(...),
    remarks: Optional[str] = Form(None),
    conn=Depends(database.get_db),
    user=Depends(require("stock", "create")),
):
    """
    POST /materials/issue -- Issues material from an Approved yarn lot to a Job Card.
    Enforces atomic serialization and quantity checks via process_job_material_issue() trigger.
    """
    uid = user.get("id") or user.get("sub") if isinstance(user, dict) else getattr(user, "id", None)
    data = {
        "job_id": job_id,
        "yarn_lot_id": yarn_lot_id,
        "qty_issued": qty_issued,
        "unit": unit,
        "issued_date": issued_date,
        "remarks": remarks,
    }
    await issue_job_material(conn, uid, data)

    is_htmx = request.headers.get("hx-request") == "true"
    if is_htmx:
        response = Response(status_code=status.HTTP_200_OK)
        response.headers["HX-Redirect"] = f"/jobs/{job_id}"
        return response

    return RedirectResponse(url=f"/jobs/{job_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/suppliers", response_class=HTMLResponse)
async def list_suppliers(request: Request):
    """
    GET /suppliers -- Master suppliers directory.
    """
    try:
        claims = await get_user_claims(request)
        user_info = {
            "id": claims.get("sub"),
            "email": claims.get("email"),
            "full_name": claims.get("user_metadata", {}).get("full_name") or claims.get("email"),
        }
    except HTTPException:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    suppliers: List[Dict[str, Any]] = []
    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await set_rls_claims(conn, claims)
                    s_rows = await conn.fetch("SELECT * FROM suppliers ORDER BY name ASC;")
                    suppliers = [dict(r) for r in s_rows]
        except Exception as e:
            logger.error(f"Error fetching suppliers: {e}")

    return templates.TemplateResponse(
        request=request,
        name="materials/suppliers.html",
        context={
            "user": user_info,
            "suppliers": suppliers,
            "current_page": "suppliers",
            "current_func": "SCM",
        },
    )


@router.post("/suppliers", response_class=RedirectResponse)
async def create_supplier(
    request: Request,
    supplier_code: str = Form(...),
    name: str = Form(...),
    contact_person: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    address: Optional[str] = Form(None),
    gst_state: Optional[str] = Form(None),
    gstin: Optional[str] = Form(None),
    tally_ledger_name: Optional[str] = Form(None),
    conn=Depends(database.get_db),
    user=Depends(require("purchase", "create")),
):
    """
    POST /suppliers -- Adds a new supplier. Restricted to purchase.create.
    """
    await conn.execute(
        """
        INSERT INTO suppliers (
            supplier_code, name, contact_person, email, phone, address,
            gst_state, gstin, tally_ledger_name
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (supplier_code) DO UPDATE 
        SET name = EXCLUDED.name, contact_person = EXCLUDED.contact_person, 
            email = EXCLUDED.email, phone = EXCLUDED.phone, address = EXCLUDED.address,
            gst_state = EXCLUDED.gst_state, gstin = EXCLUDED.gstin,
            tally_ledger_name = EXCLUDED.tally_ledger_name;
        """,
        supplier_code.strip(),
        name.strip(),
        contact_person.strip() if contact_person else None,
        email.strip() if email else None,
        phone.strip() if phone else None,
        address.strip() if address else None,
        gst_state.strip() if gst_state else None,
        gstin.strip() if gstin else None,
        tally_ledger_name.strip() if tally_ledger_name else None,
    )
    return RedirectResponse(url="/suppliers", status_code=status.HTTP_303_SEE_OTHER)
