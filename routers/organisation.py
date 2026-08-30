"""
Organisation & Roles Router — SNM Works
=============================================================================
Functions, Roles, User Role Grants, Permission Matrix, Segregation of Duties
Conflict Checking, and Role-Assigned Task Inboxes.

Permissions:
  - user_roles grant/revoke: people.approve (chief_people) OR systems.approve (chief_information)
  - profiles toggle-active: people.approve (chief_people) OR systems.approve (chief_information)
  - tasks reading: tasks.read / authenticated
  - tasks status update: tasks.update

Non-Negotiable Rules:
  - A user cannot grant roles to themselves (user_roles_no_self_grant constraint).
  - A user cannot deactivate their own user account.
  - Deactivating a user immediately revokes access within one request cycle.
  - Blocking role conflicts (e.g. purchase_officer + accounts_officer) are rejected by trigger.
=============================================================================
"""

import asyncio
import uuid
from typing import Any, Dict, List, Optional
import asyncpg
from asyncpg.exceptions import CheckViolationError, UniqueViolationError
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.status import (
    HTTP_200_OK,
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)

from auth.dependencies import current_user, require
from database import get_db

router = APIRouter(tags=["Organisation & Roles"])
templates = Jinja2Templates(directory="templates")


# ============================================================================
# 1. ORGANISATION DASHBOARD (FUNCTIONS & ROLES)
# ============================================================================

@router.get("/organisation", response_class=HTMLResponse)
async def organisation_dashboard(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tasks", "read")),
):
    """
    GET /organisation — Overview of the 11 Functions and 29 Roles with staff allocation.
    """
    funcs = await conn.fetch(
        "SELECT code, name, mandate, sort_order FROM functions ORDER BY sort_order ASC;"
    )

    roles_raw = await conn.fetch(
        """
        SELECT 
            r.code,
            r.function_code,
            r.name,
            r.level,
            r.description,
            COUNT(ur.user_id) FILTER (WHERE ur.active AND p.active) AS active_holders_count
        FROM roles r
        LEFT JOIN user_roles ur ON ur.role_code = r.code
        LEFT JOIN profiles p ON ur.user_id = p.id
        GROUP BY r.code, r.function_code, r.name, r.level, r.description
        ORDER BY r.function_code, r.level;
        """
    )

    roles_by_func: Dict[str, List[Dict[str, Any]]] = {}
    for r in roles_raw:
        f_code = r["function_code"]
        if f_code not in roles_by_func:
            roles_by_func[f_code] = []
        roles_by_func[f_code].append(dict(r))

    functions_list = []
    total_roles = len(roles_raw)
    total_assignments = 0

    for f in funcs:
        f_dict = dict(f)
        f_roles = roles_by_func.get(f["code"], [])
        f_dict["roles"] = f_roles
        f_dict["role_count"] = len(f_roles)
        for r in f_roles:
            total_assignments += r["active_holders_count"]
        functions_list.append(f_dict)

    # Active conflict count across the mill
    conflicts_count = await conn.fetchval(
        """
        SELECT COUNT(DISTINCT (ur1.user_id, rc.role_a, rc.role_b))
        FROM role_conflicts rc
        JOIN user_roles ur1 ON ur1.role_code = rc.role_a AND ur1.active = true
        JOIN user_roles ur2 ON ur2.role_code = rc.role_b AND ur2.user_id = ur1.user_id AND ur2.active = true
        JOIN profiles p ON p.id = ur1.user_id AND p.active = true;
        """
    )

    users_count = await conn.fetchval("SELECT COUNT(*) FROM profiles WHERE active = true;")

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="organisation/dashboard.html",
        context={
            "user": user_info,
            "functions": functions_list,
            "total_functions": len(functions_list),
            "total_roles": total_roles,
            "total_users": users_count,
            "total_assignments": total_assignments,
            "conflicts_count": conflicts_count or 0,
            "current_page": "organisation",
            "current_func": "PPL",
        },
    )


# ============================================================================
# 2. PERMISSION MATRIX (READ-ONLY 336 GRANTS)
# ============================================================================

@router.get("/organisation/matrix", response_class=HTMLResponse)
async def permission_matrix(
    request: Request,
    module_filter: Optional[str] = "all",
    role_filter: Optional[str] = "all",
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tasks", "read")),
):
    """
    GET /organisation/matrix — Read-only matrix of 336 role permission grants.
    """
    query = """
        SELECT 
            rp.role_code,
            r.name AS role_name,
            r.level AS role_level,
            f.code AS function_code,
            f.name AS function_name,
            rp.module,
            rp.action
        FROM role_permissions rp
        JOIN roles r ON rp.role_code = r.code
        JOIN functions f ON r.function_code = f.code
        WHERE 1=1
    """
    params = []
    param_idx = 1

    if module_filter and module_filter != "all":
        query += f" AND rp.module = ${param_idx}"
        params.append(module_filter)
        param_idx += 1

    if role_filter and role_filter != "all":
        query += f" AND rp.role_code = ${param_idx}"
        params.append(role_filter)
        param_idx += 1

    query += " ORDER BY f.sort_order, r.level, rp.module, rp.action;"
    rows = await conn.fetch(query, *params)

    modules_raw = await conn.fetch("SELECT DISTINCT module FROM role_permissions ORDER BY module;")
    modules = [m["module"] for m in modules_raw]

    roles_raw = await conn.fetch("SELECT code, name FROM roles ORDER BY name;")
    roles = [dict(r) for r in roles_raw]

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="organisation/matrix.html",
        context={
            "user": user_info,
            "permissions": [dict(r) for r in rows],
            "total_grants": len(rows),
            "modules": modules,
            "roles": roles,
            "selected_module": module_filter,
            "selected_role": role_filter,
            "current_page": "organisation",
            "current_func": "PPL",
        },
    )


# ============================================================================
# 3. SEGREGATION OF DUTIES CONFLICTS
# ============================================================================

@router.get("/organisation/conflicts", response_class=HTMLResponse)
async def list_conflicts(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tasks", "read")),
):
    """
    GET /organisation/conflicts — Segregation of duties rules and active conflict report.
    """
    conflict_rules = await conn.fetch(
        """
        SELECT 
            rc.role_a,
            r1.name AS role_a_name,
            rc.role_b,
            r2.name AS role_b_name,
            rc.reason,
            rc.severity
        FROM role_conflicts rc
        JOIN roles r1 ON rc.role_a = r1.code
        JOIN roles r2 ON rc.role_b = r2.code
        ORDER BY rc.severity DESC, rc.role_a;
        """
    )

    active_conflicts = await conn.fetch(
        """
        SELECT 
            p.id::text AS user_id,
            p.full_name,
            rc.role_a,
            r1.name AS role_a_name,
            rc.role_b,
            r2.name AS role_b_name,
            rc.reason,
            rc.severity
        FROM role_conflicts rc
        JOIN user_roles ur1 ON ur1.role_code = rc.role_a AND ur1.active = true
        JOIN user_roles ur2 ON ur2.role_code = rc.role_b AND ur2.user_id = ur1.user_id AND ur2.active = true
        JOIN profiles p ON p.id = ur1.user_id AND p.active = true
        JOIN roles r1 ON rc.role_a = r1.code
        JOIN roles r2 ON rc.role_b = r2.code
        ORDER BY p.full_name, rc.severity DESC;
        """
    )

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="organisation/conflicts.html",
        context={
            "user": user_info,
            "rules": [dict(r) for r in conflict_rules],
            "active_conflicts": [dict(c) for c in active_conflicts],
            "current_page": "organisation",
            "current_func": "PPL",
        },
    )


# ============================================================================
# 4. STAFF DIRECTORY & ROLES (USERS)
# ============================================================================

@router.get("/organisation/users", response_class=HTMLResponse)
async def list_users(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tasks", "read")),
):
    """
    GET /organisation/users — Staff members and active role assignments.
    """
    users_raw = await conn.fetch(
        """
        SELECT 
            p.id::text,
            p.full_name,
            p.active,
            p.created_at,
            COALESCE(
                json_agg(
                    json_build_object(
                        'code', r.code,
                        'name', r.name,
                        'function_code', r.function_code,
                        'level', r.level,
                        'active', ur.active
                    ) ORDER BY r.level, r.name
                ) FILTER (WHERE r.code IS NOT NULL AND ur.active = true), '[]'
            ) AS roles
        FROM profiles p
        LEFT JOIN user_roles ur ON p.id = ur.user_id AND ur.active = true
        LEFT JOIN roles r ON ur.role_code = r.code
        GROUP BY p.id, p.full_name, p.active, p.created_at
        ORDER BY p.active DESC, p.full_name ASC;
        """
    )

    can_manage = await conn.fetchval(
        "SELECT auth_can('people', 'approve') OR auth_can('systems', 'approve');"
    )

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="organisation/users.html",
        context={
            "user": user_info,
            "staff_list": [dict(u) for u in users_raw],
            "can_manage": bool(can_manage),
            "current_page": "organisation",
            "current_func": "PPL",
        },
    )


# ============================================================================
# 5. USER DETAIL & ROLE ASSIGNMENT CARD
# ============================================================================

@router.get("/organisation/users/{target_user_id}", response_class=HTMLResponse)
async def user_detail(
    target_user_id: str,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tasks", "read")),
):
    """
    GET /organisation/users/{target_user_id} — Detailed view of user roles and warnings.
    """
    p_row = await conn.fetchrow(
        """
        SELECT p.id::text, p.full_name, p.active, p.created_at
        FROM profiles p
        WHERE p.id = $1::uuid;
        """,
        target_user_id,
    )
    if not p_row:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="User not found.")

    # Held roles
    held_roles_raw = await conn.fetch(
        """
        SELECT 
            ur.role_code,
            r.name AS role_name,
            r.level,
            r.function_code,
            f.name AS function_name,
            ur.granted_at,
            ur.granted_by::text,
            p_gr.full_name AS granter_name,
            ur.active
        FROM user_roles ur
        JOIN roles r ON ur.role_code = r.code
        JOIN functions f ON r.function_code = f.code
        LEFT JOIN profiles p_gr ON ur.granted_by = p_gr.id
        WHERE ur.user_id = $1::uuid AND ur.active = true
        ORDER BY f.sort_order, r.level;
        """,
        target_user_id,
    )
    held_roles = [dict(r) for r in held_roles_raw]
    held_codes = {r["role_code"] for r in held_roles}

    # Available roles to grant
    all_roles = await conn.fetch(
        """
        SELECT r.code, r.name, r.level, r.function_code, f.name AS function_name
        FROM roles r
        JOIN functions f ON r.function_code = f.code
        ORDER BY f.sort_order, r.level, r.name;
        """
    )
    available_roles = [dict(r) for r in all_roles if r["code"] not in held_codes]

    # User active conflicts
    user_conflicts = await conn.fetch(
        "SELECT * FROM get_user_role_conflicts($1::uuid);",
        target_user_id,
    )

    can_manage = await conn.fetchval(
        "SELECT auth_can('people', 'approve') OR auth_can('systems', 'approve');"
    )

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="organisation/user_detail.html",
        context={
            "user": user_info,
            "target": dict(p_row),
            "held_roles": held_roles,
            "available_roles": available_roles,
            "conflicts": [dict(c) for c in user_conflicts],
            "can_manage": bool(can_manage),
            "is_self": str(p_row["id"]) == user.get("id"),
            "current_page": "organisation",
            "current_func": "PPL",
        },
    )


# ============================================================================
# 6. GRANT ROLE (POST)
# ============================================================================

@router.post("/organisation/users/{target_user_id}/grant", response_class=HTMLResponse)
async def grant_role(
    target_user_id: str,
    role_code: str = Form(...),
    request: Request = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(current_user),
):
    """
    POST /organisation/users/{target_user_id}/grant — Grants a role to a user.
    Guarded by:
      1. Role check: requires people.approve (chief_people) OR systems.approve (chief_information)
      2. Non-negotiable self-grant prevention: Admin cannot grant roles to themselves.
      3. Trigger check: Cannot assign role with 'block' severity conflict.
    """
    granter_id = user.get("id")
    if not granter_id:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Authentication required.")

    # 1. Authorize: people.approve or systems.approve
    can_grant = await conn.fetchval(
        "SELECT auth_can('people', 'approve') OR auth_can('systems', 'approve');"
    )
    if not can_grant:
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="Your roles do not permit granting roles. Requires Chief People or Chief Information clearance.",
        )

    # 2. Self-grant prevention
    if target_user_id == granter_id:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Segregation of duties violation: Administrators cannot grant roles to their own account.",
        )

    try:
        await conn.execute(
            """
            INSERT INTO user_roles (user_id, role_code, granted_by, active, granted_at)
            VALUES ($1::uuid, $2, $3::uuid, true, NOW())
            ON CONFLICT (user_id, role_code) DO UPDATE
            SET active = true, granted_by = $3::uuid, granted_at = NOW();
            """,
            uuid.UUID(target_user_id),
            role_code.strip(),
            uuid.UUID(granter_id),
        )
    except CheckViolationError as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Database constraint error: {str(e)}",
        )

    return RedirectResponse(url=f"/organisation/users/{target_user_id}", status_code=HTTP_303_SEE_OTHER)


# ============================================================================
# 7. REVOKE ROLE (POST)
# ============================================================================

@router.post("/organisation/users/{target_user_id}/revoke", response_class=HTMLResponse)
async def revoke_role(
    target_user_id: str,
    role_code: str = Form(...),
    request: Request = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(current_user),
):
    """
    POST /organisation/users/{target_user_id}/revoke — Revokes an active role from a user.
    """
    granter_id = user.get("id")
    can_revoke = await conn.fetchval(
        "SELECT auth_can('people', 'approve') OR auth_can('systems', 'approve');"
    )
    if not can_revoke:
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="Your roles do not permit modifying role assignments.",
        )

    await conn.execute(
        """
        UPDATE user_roles
        SET active = false
        WHERE user_id = $1::uuid AND role_code = $2;
        """,
        uuid.UUID(target_user_id),
        role_code.strip(),
    )

    return RedirectResponse(url=f"/organisation/users/{target_user_id}", status_code=HTTP_303_SEE_OTHER)


# ============================================================================
# 8. TOGGLE USER ACTIVE STATUS (POST)
# ============================================================================

@router.post("/organisation/users/{target_user_id}/toggle-active", response_class=HTMLResponse)
async def toggle_user_active(
    target_user_id: str,
    request: Request = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(current_user),
):
    """
    POST /organisation/users/{target_user_id}/toggle-active — Immediately deactivates or reactivates a user profile.
    Blocks self-deactivation to prevent administrator lockout.
    """
    admin_id = user.get("id")
    can_toggle = await conn.fetchval(
        "SELECT auth_can('people', 'approve') OR auth_can('systems', 'approve');"
    )
    if not can_toggle:
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="Your roles do not permit altering user account active status.",
        )

    # Prevent self-deactivation
    if target_user_id == admin_id:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own user account. Another administrator must perform this action.",
        )

    await conn.execute(
        """
        UPDATE profiles
        SET active = NOT active
        WHERE id = $1::uuid;
        """,
        uuid.UUID(target_user_id),
    )

    return RedirectResponse(url=f"/organisation/users/{target_user_id}", status_code=HTTP_303_SEE_OTHER)


# ============================================================================
# 9. MY TASKS & INBOX (GET)
# ============================================================================

@router.get("/tasks", response_class=HTMLResponse)
@router.get("/organisation/tasks", response_class=HTMLResponse)
async def my_tasks_inbox(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tasks", "read")),
):
    """
    GET /tasks — My Tasks inbox, dynamically routed based on all roles held by caller.
    """
    task_rows = await conn.fetch("SELECT * FROM my_tasks();")

    user_info = {
        "id": user.get("id"),
        "email": user.get("email"),
        "full_name": user.get("claims", {}).get("user_metadata", {}).get("full_name") or user.get("email"),
    }

    return templates.TemplateResponse(
        request=request,
        name="tasks/list.html",
        context={
            "user": user_info,
            "tasks": [dict(t) for t in task_rows],
            "total_count": len(task_rows),
            "current_page": "tasks",
            "current_func": "OPS",
        },
    )


# ============================================================================
# 10. UPDATE TASK STATUS (POST)
# ============================================================================

@router.post("/tasks/{task_id}/status", response_class=HTMLResponse)
async def update_task_status(
    task_id: str,
    status: str = Form(...),
    request: Request = None,
    conn: asyncpg.Connection = Depends(get_db),
    user: Dict[str, Any] = Depends(require("tasks", "update")),
):
    """
    POST /tasks/{task_id}/status — Transition task status (open, in progress, done, cancelled).
    """
    if status not in ("open", "in progress", "blocked", "done", "cancelled"):
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Invalid task status.")

    await conn.execute(
        """
        UPDATE tasks
        SET 
            status = $1,
            closed_at = CASE WHEN $1 IN ('done', 'cancelled') THEN NOW() ELSE NULL END,
            assigned_user = COALESCE(assigned_user, $2::uuid)
        WHERE id = $3::uuid;
        """,
        status,
        uuid.UUID(user.get("id")),
        uuid.UUID(task_id),
    )

    return RedirectResponse(url="/tasks", status_code=HTTP_303_SEE_OTHER)
