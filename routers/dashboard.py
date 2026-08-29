import logging
from typing import Any, Dict, List, Optional
import asyncpg
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import get_db
from auth.jwt import decode_access_token
from auth.middleware import set_rls_claims

logger = logging.getLogger("snm_works.dashboard")
router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="templates")

ORGANIZATION_FUNCTIONS: List[Dict[str, Any]] = [
    {
        "code": "EXEC",
        "name": "Executive",
        "description": "Enterprise governance, strategic oversight, and corporate authorization.",
        "chief": "chief_executive",
        "managers": [],
        "operatives": [],
        "modules": ["systems", "people", "audit"],
    },
    {
        "code": "OPS",
        "name": "Operating",
        "description": "Shop-floor production, loom management, shifts, and equipment maintenance.",
        "chief": "chief_operating",
        "managers": ["production_manager"],
        "operatives": ["shift_supervisor", "machine_operator", "maintenance_officer"],
        "modules": ["jobs", "downtime", "despatch"],
    },
    {
        "code": "QUA",
        "name": "Quality",
        "description": "Inspection plans, tensile testing, defect tracking, and CAPA resolution.",
        "chief": "chief_quality",
        "managers": ["qa_manager"],
        "operatives": ["lab_analyst", "line_inspector"],
        "modules": ["qc", "tests", "capa"],
    },
    {
        "code": "TEC",
        "name": "Technical",
        "description": "Yarn specifications, weave constructions, recipe design, and MIL standards.",
        "chief": "chief_technical",
        "managers": ["product_developer", "process_engineer"],
        "operatives": [],
        "modules": ["constructions", "specifications", "recipes"],
    },
    {
        "code": "COM",
        "name": "Commercial",
        "description": "Customer accounts, order books, SKU catalogues, and export logistics.",
        "chief": "chief_commercial",
        "managers": [],
        "operatives": ["sales_executive", "export_executive"],
        "modules": ["skus", "customers"],
    },
    {
        "code": "SCM",
        "name": "Supply Chain",
        "description": "Raw material procurement, yarn lot inventory, and warehouse storage.",
        "chief": "chief_supply_chain",
        "managers": [],
        "operatives": ["purchase_officer", "store_keeper"],
        "modules": ["stock", "purchase"],
    },
    {
        "code": "FIN",
        "name": "Financial",
        "description": "Costing matrices, BOM economics, and accounting reconciliation.",
        "chief": "chief_financial",
        "managers": ["costing_analyst"],
        "operatives": ["accounts_officer"],
        "modules": ["costing"],
    },
    {
        "code": "PPL",
        "name": "People",
        "description": "Personnel records, shift assignments, and task routing.",
        "chief": "chief_people",
        "managers": [],
        "operatives": ["hr_officer"],
        "modules": ["people", "tasks"],
    },
    {
        "code": "INF",
        "name": "Information",
        "description": "System access, security posture, logs, and infrastructure health.",
        "chief": "chief_information",
        "managers": ["system_admin"],
        "operatives": [],
        "modules": ["systems"],
    },
    {
        "code": "KNW",
        "name": "Knowledge",
        "description": "Standard operating procedures, document control, and revision management.",
        "chief": "chief_knowledge",
        "managers": [],
        "operatives": ["document_controller"],
        "modules": ["audit"],
    },
    {
        "code": "CMP",
        "name": "Compliance",
        "description": "Regulatory conformity, audit chain verification, and defence supply audits.",
        "chief": "chief_compliance",
        "managers": [],
        "operatives": [],
        "modules": ["audit"],
    },
]


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_view(
    request: Request,
    func: Optional[str] = None,
):
    """
    Protected dashboard route.
    Authenticates token from cookie, sets RLS claims inside transaction,
    queries SELECT my_roles() and profiles directly from PostgreSQL,
    and renders the 11 function cards.
    """
    token = request.cookies.get("access_token")
    if not token:
        # Fallback to Authorization header if provided
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]

    if not token:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    try:
        claims = decode_access_token(token)
    except Exception:
        response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
        response.delete_cookie(key="access_token", path="/")
        return response

    user_id = claims.get("sub")
    roles = []
    profile_record = None
    db_connected = False

    # Attempt to query database if pool is available or can be connected
    import database
    if database.pool is None:
        await database.init_db_pool()

    if database.pool is not None:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    # Inject transaction-scoped RLS claims
                    await set_rls_claims(conn, claims)

                    # 1. Query SELECT my_roles() directly from PostgreSQL
                    role_rows = await conn.fetch("SELECT my_roles()")
                    roles = [r[0] for r in role_rows]
                    db_connected = True

                    # 2. Query user profile from database
                    if user_id:
                        profile_record = await conn.fetchrow(
                            "SELECT full_name, role FROM profiles WHERE id = $1",
                            user_id
                        )
        except Exception as exc:
            logger.warning(f"Database query error during dashboard load: {exc}")
            db_connected = False

    full_name = None
    profile_role = None
    if profile_record:
        full_name = profile_record["full_name"]
        profile_role = profile_record["role"]
    else:
        full_name = (
            claims.get("user_metadata", {}).get("full_name")
            or claims.get("name")
            or (claims.get("email", "").split("@")[0].replace(".", " ").title() if claims.get("email") else "User")
        )
        profile_role = claims.get("role", "owner")

    user_info = {
        "id": user_id,
        "email": claims.get("email"),
        "full_name": full_name,
        "profile_role": profile_role,
    }

    # Filter functions if func query param is provided
    functions_to_display = ORGANIZATION_FUNCTIONS
    if func and func.upper() != "ALL":
        functions_to_display = [f for f in ORGANIZATION_FUNCTIONS if f["code"] == func.upper()]

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "user": user_info,
            "roles": roles,
            "functions": functions_to_display,
            "current_func": func.upper() if func else "ALL",
        }
    )
