# AGENTS.md — SNM Works (Python / FastAPI)

Project brief. Read this in full before writing a single line of code.

---

## 1. What this is

A works management system for **Swadeshi Niwar Mills**, a technical textiles
manufacturer in Kanpur, India. Owner: Yash Khandelwal.

Products: **narrow wovens** (webbing, tape, slings), **fabrics** (coated and
uncoated technical cloth), **cordage** (rope, cord, braid). A significant part
of output is **defence specification** supplied to Ordnance Factory Kanpur,
manufactured to MIL standards.

5–15 users at launch. Shop floor on smartphones. Owner and office on phones and
desktop.

**Yash is not a professional developer.** Explain things plainly. Do the work
in the repository directly rather than handing him files. When something breaks,
read the code and find the cause — do not ask him to interpret a traceback.

---

## 2. Why FastAPI

FastAPI was chosen over Django for three reasons that are specific to this
project:

1. **Multiple clients.** The same API will serve the web interface, an Android
   and iOS PWA for the shop floor, and eventually machine integrations. Django's
   form layer is browser-only; FastAPI's JSON responses are client-agnostic.

2. **Testability.** pytest-first, async from the ground up. The textile
   calculation library, the permission model and every business rule are unit
   tested without a running server.

3. **Explicit permission model.** Permissions are already in the database as
   `role_permissions` rows. FastAPI lets us call `auth_can(module, action)`
   directly rather than mapping to Django's auth model.

Django would have been right if this were a single-client admin tool. It is not.

---

## 3. The database — already exists, do not recreate

```
Project ref  eayrmjmzjokeeuwazmjy
URL          https://eayrmjmzjokeeuwazmjy.supabase.co
Region       ap-south-1 (Mumbai)
Publishable  sb_publishable_MXk1JNE2Fb77myKjBfNLMg_i24vbA2k
```

**The `sb_secret_` key must never appear in any file, log or message.**
Do not ask for it. Do not mention it.

Seven SQL migrations have been run against this database:

| File | What it created |
|---|---|
| 01_schema.sql | All production tables, `auth_role()`, RLS on everything, audit triggers |
| 02_audit_chain.sql | SHA-256 hash-chained audit log, `verify_audit_chain()` |
| 03_security_report.sql | `rls_report()`, `security_posture()` |
| 04_reference_data.sql | Parameter library, dropdown masters, defect codes |
| 05_specifications.sql | Generic spec model, `spec_check_plan()` |
| 06_load_mil4088.sql | MIL-W-4088K, 60 variants, 560 requirements |
| 07_roles.sql | 11 functions, 29 roles, 336 permissions, task routing |

A further file `08_lockdown.sql` adds `FORCE ROW LEVEL SECURITY` on every
table and creates the `snm_app` Postgres role. **Run this before the
application connects for the first time.** See section 5 for detail.

Key tables: `profiles`, `functions`, `roles`, `user_roles`, `role_permissions`,
`role_conflicts`, `tasks`, `specifications`, `spec_variants`, `spec_requirements`,
`spec_defects`, `spec_sampling`, `jobs`, `qc_checks`, `lab_tests`, `capa`,
`dye_recipes`, `constructions`, `downtime`, `despatch`, `skus`, `customers`,
`costing`, `masters`, `param_library`, `audit_log`.

Key SQL functions: `my_roles()`, `auth_can(module, action)`, `has_role(code)`,
`my_tasks()`, `spec_check_plan(variant_id)`, `verify_audit_chain()`,
`security_posture()`.

**Schema ownership stays in SQL files.** SQLAlchemy models map to existing
tables with `autoload_with` or explicit column definitions. They do not own the
schema. New tables get a numbered `.sql` file in `sql/`, written by the agent,
reviewed and run by Yash in the Supabase SQL editor. Never auto-migrate against
the live database.

---

## 4. Existing code to keep

The `snm-works` folder contains a Vite + React prototype. Do not delete it —
archive it into a subfolder `_react_archive/` at the start. The SQL files and
the `CLAUDE.md` inside it can be copied to `sql/` and the project root.

The React prototype is a working functional reference. When in doubt about
what a screen should do, look there.

---

## 5. The security model — the most important section

**Read this before writing any database code.**

### The risk

FastAPI opens connections to Postgres as one database user, reused across
requests. Left alone:

- If that user owns the tables or has superuser, RLS policies do not apply and
  every permission check is decorative.
- If connection pooling is naive, one user's JWT claims linger on a connection
  and the next request runs as them.

### The fix — three layers

**Layer 1: database-level lock (08_lockdown.sql)**

```sql
-- FORCE means even the table owner is bound by policies
alter table <every table> force row level security;

-- snm_app is not superuser, not table owner, and cannot bypass RLS
create role snm_app with login password '...' nobypassrls nosuperuser;
grant authenticated to snm_app;
grant select, insert, update on all tables in schema public to snm_app;
-- NO delete granted to snm_app anywhere, deliberately
```

This holds even if the application code is completely wrong.

**Layer 2: per-request identity (middleware)**

Every request, inside its transaction, before any other query:

```python
# auth/middleware.py
async def set_rls_claims(conn, claims: dict):
    """
    SET LOCAL is transaction-scoped. Plain SET would persist on a pooled
    connection and leak one user's identity into the next request.
    Always LOCAL. No exceptions.
    """
    await conn.execute("SET LOCAL ROLE authenticated")
    await conn.execute(
        "SELECT set_config('request.jwt.claims', $1, true)",
        [json.dumps(claims)]
    )
```

`true` as the third argument to `set_config` also means transaction-scoped.

**Layer 3: the test that proves it**

```python
async def test_supervisor_cannot_read_costing(supervisor_client):
    """
    RLS, not Python, must be what refuses this.
    If this test has never failed, it proves nothing.
    Write it first. Watch it fail. Then build the middleware.
    """
    resp = await supervisor_client.get("/api/costing/")
    assert resp.json() == []
```

Run this test before the middleware exists. It must fail. After the middleware,
it must pass. Only then do you know the database is doing the work, not Python.

### Connection rules

- Connect as `snm_app`, never as `postgres` or the Supabase pooler superuser.
- One transaction per request. Use a dependency that opens a transaction,
  sets the claims, yields, and commits or rolls back.
- Never share a database session across requests.
- If using asyncpg directly: `await conn.execute("SET LOCAL ROLE ...")` at the
  start of every transaction, inside the transaction.

---

## 6. Project layout

```
snm_works/
├── _react_archive/          React prototype moved here, not deleted
├── sql/                     numbered migrations, source of schema truth
│   ├── 01_schema.sql
│   ├── 02_audit_chain.sql
│   ├── 03_security_report.sql
│   ├── 04_reference_data.sql
│   ├── 05_specifications.sql
│   ├── 06_load_mil4088.sql
│   ├── 07_roles.sql
│   └── 08_lockdown.sql
├── pyproject.toml
├── .env.example             committed; .env is not
├── .gitignore
├── main.py                  FastAPI app, lifespan, middleware
├── config.py                settings from .env
├── database.py              asyncpg pool, get_conn dependency
├── auth/
│   ├── jwt.py               Supabase JWT validation
│   ├── middleware.py        RLS claim injection
│   ├── dependencies.py     current_user, require_permission
│   └── models.py           User, Role pydantic models
├── textiles/                pure calculation library, NO framework imports
│   ├── __init__.py
│   ├── units.py             conversions
│   ├── narrow.py            narrow fabric calculations
│   ├── fabric.py            broad fabric calculations
│   ├── cordage.py           rope and braid calculations
│   └── limits.py            verdict logic, the four limit kinds
├── routers/
│   ├── organisation.py      functions, roles, user_roles, tasks
│   ├── specifications.py    masterbase, variants, requirements
│   ├── engineering.py       constructions, calculations
│   ├── production.py        jobs, downtime, despatch
│   ├── quality.py           qc_checks, lab_tests, capa
│   ├── commercial.py        customers, skus, costing
│   └── system.py            security posture, audit log
├── templates/               Jinja2 templates for the web UI
├── static/
│   ├── css/
│   └── js/
└── tests/
    ├── conftest.py          fixtures including supervisor_client
    ├── test_rls.py          the security tests
    ├── test_textiles.py     calculation library tests
    └── test_api.py          endpoint tests
```

---

## 7. Dependencies

```toml
# pyproject.toml
[project]
name = "snm-works"
version = "0.1.0"
requires-python = ">=3.12"

dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "asyncpg>=0.29",
    "python-jose[cryptography]>=3.3",   # JWT
    "python-dotenv>=1.0",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "jinja2>=3.1",
    "python-multipart>=0.0.9",          # form data
    "httpx>=0.27",                       # async test client
    "reportlab>=4.2",                    # PDF certificates
]

[project.optional-dependencies]
dev = [
    "pytest>=8.2",
    "pytest-asyncio>=0.23",
    "anyio>=4.4",
    "ruff>=0.4",
]
```

---

## 8. Core patterns

### The request transaction dependency

```python
# database.py
from contextlib import asynccontextmanager
import asyncpg

pool: asyncpg.Pool | None = None

async def get_db(request: Request):
    """
    Yields one connection per request, inside a transaction.
    The middleware sets RLS claims inside this transaction.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn
```

### Permission dependency

```python
# auth/dependencies.py
def require(module: str, action: str):
    async def dep(
        conn=Depends(get_db),
        user=Depends(current_user),
    ):
        ok = await conn.fetchval(
            "SELECT auth_can($1, $2)", module, action
        )
        if not ok:
            raise HTTPException(403, f"Your roles do not permit {action} on {module}")
        return user
    return dep

# usage in a router
@router.get("/costing/")
async def list_costing(
    conn=Depends(get_db),
    _=Depends(require("costing", "read")),
):
    ...
```

### Generic register pattern

Every register (jobs, QC, tests, CAPA, recipes) shares the same shape. Define
a `RegisterConfig` dataclass with table name, allowed columns, permission
module, and any computed fields. One generic router handles them all. Adding a
register means adding a config, not a new router.

```python
@dataclass
class RegisterConfig:
    table: str
    module: str          # for auth_can()
    list_columns: list[str]
    computed: dict       # key: fn(row) -> value
    order_by: str = "created_at"
    cancellable: bool = False  # cancelled, never deleted
```

---

## 9. The textile calculation library

Denier is grams per 9000 m, so one end of D denier weighs `D/9000` g/m.

```python
# textiles/narrow.py
def warp_gpm(ends: int, warp_denier: float, warp_crimp_pct: float = 0.0) -> float:
    return ends * warp_denier * (1 + warp_crimp_pct / 100) / 9000

def weft_gpm(picks_per_cm: float, width_mm: float,
             weft_denier: float, weft_crimp_pct: float = 0.0) -> float:
    return picks_per_cm * 100 * (width_mm / 1000) * weft_denier * (1 + weft_crimp_pct / 100) / 9000

def theoretical_break_kgf(ends: int, denier: float,
                           tenacity_g_per_den: float, efficiency_pct: float = 85.0) -> float:
    return ends * denier * tenacity_g_per_den * (efficiency_pct / 100) / 1000

# textiles/fabric.py
def warp_gsm(epi: float, warp_denier: float, warp_crimp_pct: float = 0.0) -> float:
    return epi * 39.37 * warp_denier * (1 + warp_crimp_pct / 100) / 9000

def cover_factor_total(epi: float, ppi: float,
                       warp_denier: float, weft_denier: float) -> float:
    kw = epi * warp_denier ** 0.5 / 28
    kf = ppi * weft_denier ** 0.5 / 28
    return kw + kf - (kw * kf) / 28

# textiles/limits.py — the four limit kinds
from enum import Enum

class LimitKind(str, Enum):
    nominal = "nominal"
    minimum = "minimum"
    maximum = "maximum"
    range_  = "range"

def verdict(kind: LimitKind, spec: float, actual: float,
            tolerance: float = 0.0, upper: float = None) -> str:
    if kind == LimitKind.minimum:
        return "PASS" if actual >= spec else "FAIL"
    if kind == LimitKind.maximum:
        return "PASS" if actual <= spec else "FAIL"
    if kind == LimitKind.range_:
        return "PASS" if spec <= actual <= upper else "FAIL"
    return "PASS" if abs(actual - spec) <= tolerance else "FAIL"
```

**Tests that must exist and pass:**

- MIL-W-4088K Type VIII: 320 ends × 840 denier × 8.5 g/den × 85% efficiency
  → approximately 4,282 lbf (≥ 4,000 lbf spec minimum, gap is efficiency)
- Cover factor above 28 in one direction → flag physically impossible
- Every unit conversion tested in both directions
- All four limit kinds tested including boundary conditions

These are in `tests/test_textiles.py`.

---

## 10. Interface conventions

Shop floor uses phones in bright light with dusty hands. Narrow-first.

**Palette** — olive `#474B2F`, machine black `#1B2017`, greige `#E9E5DA`,
paper `#F6F4EE`, line `#CFC8B6`, pass `#3F6B34`, fail `#A82914`, hold `#9A6407`

**Type** — Barlow Condensed for headings, IBM Plex Sans for body, IBM Plex Mono
for numbers and identifiers

- Numbers right-aligned, monospace
- Touch targets minimum 44 px
- Errors shown on screen in plain language, never swallowed
- HTMX for interactivity — no SPA, server-rendered, no build step for JS
- Alpine.js for local state (dropdown toggles, form hints)

---

## 11. Non-negotiable rules

1. `sb_secret_` key never in any file, log or message
2. Every table has RLS enabled, FORCED, and at least one policy
3. `audit_log` gets no UPDATE and no DELETE path, for any role, ever
4. Application connects as `snm_app` — not `postgres`, not a superuser
5. `costing` readable only by roles holding `costing.read`
6. QC and lab verdicts computed in Postgres as generated columns, never sent from Python
7. A person cannot approve a record they entered — database constraint, not Python
8. For `is_critical` requirements: release test is `min(specimens) >= limit`, never average (MIL-W-4088K 3.6.1)
9. Jobs and specs are cancelled, never deleted
10. Deactivating a user ends their access within one request cycle

---

## 12. Build order — ship each stage before starting the next

- [ ] Python install, project scaffold, pyproject.toml, .gitignore, .env
- [ ] asyncpg pool, request transaction dependency
- [ ] JWT validation and RLS middleware — **with the failing test first**
- [ ] `security_posture()` endpoint proving the database-level lock
- [ ] `textiles/` library with full pytest coverage
- [ ] Generic register framework
- [ ] Organisation: functions, roles, who holds what, conflicts, my tasks
- [ ] Specifications browser
- [ ] Job cards and QC entry (mobile first)
- [ ] Lab tests, CAPA, approval workflow
- [ ] Constructions with live calculation
- [ ] Inspection plans from `spec_check_plan()`
- [ ] Materials: yarn lots, GRN, incoming test, issue against job
- [ ] Traceability: yarn lot → job → roll → certificate
- [ ] Test certificate PDF
- [ ] Costing (owner only)
- [ ] Client order summaries by email
- [ ] Deploy as PWA

---

## 13. Windows-specific notes

Yash is on Windows with PowerShell. All commands must work in PowerShell.

```powershell
# Install Python from python.org first, then:
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn main:app --reload
pytest
```

If PowerShell blocks scripts: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

Do not use Unix-specific paths or `&&` chaining. Use `;` in PowerShell.

---

## 14. How to work with Yash

- Commit after each working piece with a message saying what changed and why
- Never run SQL against the live database — write it to `sql/`, explain it,
  let Yash run it in the Supabase SQL editor
- If a request would be a mistake, say so before building it
- Keep files under 400 lines — split when they grow past that
- Check facts against `MIL-W-4088K.md`, not memory. A breaking strength figure
  was quoted wrong from memory and had to be corrected. That must not happen again
- The eleven functions and 29 roles are in section 15. Refer to them when
  building permissions rather than inventing new ones.

---

## 15. The eleven functions and 29 roles

Every permission check references these role codes.

| Function code | Function | Chief role | Manager roles | Operative roles |
|---|---|---|---|---|
| EXEC | Executive | chief_executive | — | — |
| OPS | Operating | chief_operating | production_manager | shift_supervisor, machine_operator, maintenance_officer |
| QUA | Quality | chief_quality | qa_manager | lab_analyst, line_inspector |
| TEC | Technical | chief_technical | product_developer, process_engineer | — |
| COM | Commercial | chief_commercial | — | sales_executive, export_executive |
| SCM | Supply Chain | chief_supply_chain | — | purchase_officer, store_keeper |
| FIN | Financial | chief_financial | costing_analyst | accounts_officer |
| PPL | People | chief_people | — | hr_officer |
| INF | Information | chief_information | system_admin | — |
| KNW | Knowledge | chief_knowledge | — | document_controller |
| CMP | Compliance | chief_compliance | — | — |

Permission actions: `read | create | update | approve | release`

Modules: `jobs, qc, tests, capa, recipes, constructions, specifications, skus,
customers, costing, downtime, despatch, stock, purchase, people, tasks,
systems, audit`

Segregation of duties (role_conflicts table):
- `chief_operating` ↔ `chief_quality`: warn (unavoidable at small size)
- `lab_analyst` ↔ `chief_quality`: warn
- `chief_commercial` ↔ `chief_financial`: warn
- `purchase_officer` ↔ `store_keeper`: warn
- `purchase_officer` ↔ `accounts_officer`: **block**

Yash currently holds all eleven chief roles. His Supabase user id is
`yashkhandelwal95@gmail.com`, profile role `owner`.

---

## 16. MIL-W-4088K quick reference

Full data in `MIL-W-4088K.md`. Key facts for calculations:

- Type VIII: width 1.71875 ± 0.0625 in, breaking strength **≥ 4000 lb** minimum,
  weight ≤ 1.60 oz/yd, thickness 0.040–0.070 in, ends 166 F+B, picks 18/in
- Breaking strength is a **floor on every individual specimen**, never an average
- Yarn denier class 1: 840/140 warp and filling
- Weave: 2 up 2 down herringbone twill, 1 reversal at centre
- Strongest type: XXVI at 15,000 lb minimum
- All 30 types, both class configurations: 60 variants, 560 requirements in database

---

## Current State

Built and working:

- Sign in, dashboard, base template
- SKUs with image upload and SNM branding overlay
- Marketing queue, campaign posts, mock publishing to 5 platforms
- LinkedIn OAuth and settings page
- Job cards at /jobs
- QC checks at /qc (just completed)

Database tables modified beyond original schema:

- skus — added post_status, post_draft, platform_results, post_approved_at, post_published_at, rejection_reason, catalogue_visible
- campaigns — new table created
- platform_connections — new table created

Next to build in order:

- Lab tests /lab-tests
- CAPA /capa
- Constructions /constructions
- Dye recipes /recipes
- Downtime /downtime
- Despatch /despatch
- Organisation and roles /organisation
- My Tasks /tasks
- Costing /costing
- Test certificate PDF

Supabase project: eayrmjmzjokeeuwazmjy, region ap-south-1

