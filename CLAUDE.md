# CLAUDE.md — SNM Works

Project context for Claude Code. Read this before doing anything.

---

## 1. What this is

A works management application for **Swadeshi Niwar Mills**, a technical
textiles manufacturer in Kanpur, India, owned by Yash Khandelwal.

The mill makes **narrow wovens** (webbing, tape, slings), **fabrics** (coated
and uncoated technical cloth) and **cordage** (rope, cord, braid). A significant
part of the output is **defence specification**, supplied to Ordnance Factory
Kanpur, manufactured to MIL standards.

Around 5–15 users. Shop floor staff enter data on their own smartphones. The
owner and office use phones and desktop.

**Yash is not a professional developer.** Explain what you are doing in plain
terms. Never leave him with a command he does not understand. When something
breaks, find the cause rather than asking him to read a stack trace.

---

## 2. Current state — what already exists

### Supabase project (live, do not recreate)

```
Project ref  eayrmjmzjokeeuwazmjy
URL          https://eayrmjmzjokeeuwazmjy.supabase.co
Region       ap-south-1 (Mumbai)
Publishable  sb_publishable_MXk1JNE2Fb77myKjBfNLMg_i24vbA2k
```

The publishable key is safe in the browser — RLS is what protects the data.
**Never** put the `sb_secret_` key in any file, and never ask for it.

Yash's account: `yashkhandelwal95@gmail.com`, profile role `owner`.

### Database, already migrated

Seven SQL files have been run. If `sql/` is empty, recreate them from this
brief; if they exist, do not re-run without checking.

| File | Contents |
|---|---|
| `01_schema.sql` | Core tables, `auth_role()`, RLS on everything, audit triggers |
| `02_audit_chain.sql` | SHA-256 hash-chained audit log, `verify_audit_chain()` |
| `03_security_report.sql` | `rls_report()` — policy coverage from the catalogue |
| `04_reference_data.sql` | Parameter library, dropdown masters, defect codes |
| `05_specifications.sql` | Generic specification model, `spec_check_plan()` |
| `06_load_mil4088.sql` | MIL-W-4088K into the spec model, 60 variants, 560 requirements |
| `07_roles.sql` | 11 functions, 29 roles, 336 permissions, task routing |

Verified working: 33+ policies, audit chain returns `OK`, sign-in works.

### Frontend, partially built

Vite + React (JavaScript, not TypeScript) at `C:\Users\info\snm-works`.
If the current folder differs, use the current folder and tell Yash.

```
src/
├── App.jsx                    auth gate, sidebar, routing
├── index.css                  all styling, no framework
├── lib/
│   ├── supabase.js            client + loadSession()
│   └── moduleKit.js           Module base class
├── modules.js                 11 module definitions
├── components/Register.jsx    generic list + form for any Module
└── screens/Specifications.jsx spec browser and editor
```

---

## 3. Architecture decisions, and why

Do not reverse these without discussing it. Each was chosen against an
alternative for a reason.

**Postgres with row level security, not application-layer checks.**
Supabase exposes a REST endpoint for every table. A permission check in React
is decoration. Every rule lives in a policy.

**Roles are data, not an enum.** One person holds many roles; permissions are
the union. Hiring is a row in `user_roles`, never a code change. Yash currently
holds all 11 chief roles.

**Work is assigned to a role, not a person.** Tasks route to `qa_manager`, not
to a name. Replace the holder and work reroutes itself.

**A requirement is a row, not a column.** The specification model stores
`parameter + limit kind + value + unit + method + clause`. This fits MIL, IS,
ISO, ASTM and customer drawings without schema changes. An earlier design with
`width_in` and `break_min_lb` columns was scrapped for this reason.

**Four limit kinds, because specs use all four.** `nominal ±`, `minimum`,
`maximum`, `range`. Width is nominal, thickness is a range, weight is a ceiling,
breaking strength is a floor. Treating a maximum as a target releases bad
material.

**Verdicts are computed in the database.** `qc_checks.verdict` is a generated
column. The browser sends a reading, never a verdict.

**The audit log is append-only.** No UPDATE or DELETE policy exists on
`audit_log` for any role, including owner. Rows are hash-chained; altering one
breaks every hash after it. Do not add a delete policy for any reason.

**Job cards are cancelled, never deleted.** Same for specifications.

**Hash chaining, not blockchain.** Gives tamper evidence without publishing
commercial data or paying gas. Considered and rejected deliberately.

---

## 4. Non-negotiable rules

Violating any of these is a defect, however convenient it seems.

1. `sb_secret_` key never appears in any file, log or message.
2. Every table has RLS enabled and at least one policy.
3. `audit_log` gets no UPDATE and no DELETE policy, ever.
4. `costing` is readable only by roles holding `costing.read`. Today that is
   Chief Financial and Chief Executive.
5. QC and lab verdicts are computed server-side.
6. A person cannot approve a record they entered. Enforce in the database.
7. For a requirement flagged `is_critical`, the release test is
   `min(all specimens) >= limit`, never the average.
   MIL-W-4088K 3.6.1 is explicit about this.
8. No `localStorage` for business data. Supabase is the record.
9. A job with an open QC or lab failure cannot be despatched without a recorded
   override reason.
10. Deactivating a user must end their access immediately.

---

## 5. The eleven functions

| Code | Function | Owns |
|---|---|---|
| EXEC | Executive | Strategy, capital, key relationships |
| OPS | Operating | Production, plant, delivery |
| QUA | Quality | Specs, inspection, lab, release, CAPA |
| TEC | Technical | Constructions, recipes, development |
| COM | Commercial | Sales, tenders, pricing, catalogue |
| SCM | Supply Chain | Procurement, stores, lot identity, logistics |
| FIN | Financial | Costing, accounts, GST, working capital |
| PPL | People | Manning, competency, statutory |
| INF | Information | Systems, access, data, backups |
| KNW | Knowledge | Specification masterbase, SOPs, training |
| CMP | Compliance | Registrations, licences, contract obligations |

29 roles sit beneath these at three levels: `chief`, `manager`, `operative`.
Permissions are `(role_code, module, action)` where action is
`read | create | update | approve | release`.

Key helper functions in the database:

```sql
my_roles()                     -- every role the caller holds
auth_can(module, action)       -- union of permissions across those roles
has_role(role_code)            -- specific role check
my_tasks()                     -- work routed to any role the caller holds
```

Use `auth_can()` in new RLS policies. The older `auth_role()` predates the role
model and should be phased out.

---

## 6. Textile domain knowledge

Needed for the calculation engine. Denier is grams per 9000 m, so one end of
D denier weighs `D/9000` grams per metre. Everything follows from that.

**Narrow fabrics — reported per running metre, not GSM**

```
warp g/m  = ends × warp_denier × (1 + warp_crimp/100) / 9000
weft g/m  = picks_per_m × width_m × weft_denier × (1 + weft_crimp/100) / 9000
equiv GSM = total g/m / width_m
ends/inch = total_ends / (width_mm / 25.4)
```

**Broad fabric — reported as GSM**

```
warp GSM = EPI × 39.37 × warp_denier × (1 + warp_crimp/100) / 9000
weft GSM = PPI × 39.37 × weft_denier × (1 + weft_crimp/100) / 9000
oz/yd²   = GSM / 33.906
cover warp = EPI × sqrt(warp_denier) / 28
cover weft = PPI × sqrt(weft_denier) / 28
cover total = Kw + Kf − (Kw × Kf)/28
```

Cover factor above ~28 in one direction means the yarns physically will not fit.
20–24 total is a firm weatherable cloth; below 14 is open and needs coating to
hold a hydrostatic head.

**Cordage**

```
total yarns = carriers × yarns_per_carrier + core_yarns
g/m         = total_yarns × yarn_denier × (1 + contraction/100) / 9000
tex         = g/m × 1000
twist factor α_tex = (TPM / 100) × sqrt(tex)
```

**Strength**

```
theoretical break (kgf) = total_yarns × denier × tenacity(g/den) × efficiency / 1000
```

Translation efficiency is yarn-to-fabric loss, typically 80–90%. It must be
calibrated against real break tests, not assumed. HT nylon 6,6 ≈ 8.5 g/den,
HT polyester ≈ 7.5 g/den.

**Conversions**

```
denier → dtex   × 10/9        denier → tex   ÷ 9
denier → Ne     5315/denier    denier → Nm    9000/denier
GSM → oz/yd²    ÷ 33.906       kgf → lbf      × 2.20462
oz/yd → g/m     × 31.0035      inch → mm      × 25.4
kgf → N         × 9.80665
```

**Testing conditions.** Textile testing requires standard atmosphere per
ISO 139: 20 ± 2 °C, 65 ± 4 % RH. Nylon strength moves several percent with
moisture. Record temperature, humidity and conditioning time with every lab
result, and block entry from an instrument whose calibration has expired.

---

## 7. Conventions

- **JavaScript, not TypeScript.** Match what exists.
- **No CSS framework.** All styling in `src/index.css` using the variables
  already defined. Palette: olive `#474B2F`, machine black `#1B2017`, greige
  `#E9E5DA`, paper `#F6F4EE`. Fonts: Barlow Condensed for headings, IBM Plex
  Sans for body, IBM Plex Mono for numbers and identifiers.
- **Adding a register means adding a definition to `modules.js`**, never writing
  another CRUD screen. If a new register needs a new screen, say why first.
- **Numbers right-aligned, in mono.** Identifiers in mono.
- **Errors are shown on screen**, in words a supervisor can act on, never
  swallowed into a console.
- **Mobile matters.** Shop floor uses phones. Test narrow layouts.
- SQL files are numbered and sequential in `sql/`. New migrations get the next
  number and are never edited once run.
- Every loader carries its own data. Do not write a migration that reads from a
  table another file was supposed to populate — that failure mode already cost a
  day.

---

## 8. Build order

Ship each stage usable before starting the next.

- [x] Auth, profiles, RLS, audit chain
- [x] Specification model, MIL-W-4088K loaded
- [x] Functions, roles, permissions, task routing
- [x] Generic Module base class and register renderer
- [ ] **Rewrite RLS policies to use `auth_can()`** rather than `auth_role()`
- [ ] Role and permission admin screen — assign roles, see conflicts
- [ ] My Tasks screen driven by `my_tasks()`
- [ ] Inspection plans generated from spec requirements
- [ ] QC entry on mobile, with the four limit kinds
- [ ] Approval workflow — entered → reviewed → approved → released
- [ ] Materials: yarn lots, GRN, incoming test, issue against job
- [ ] Traceability: yarn lot → job → roll → certificate
- [ ] Test certificate generation
- [ ] Costing, owner only
- [ ] Client order summaries by email
- [ ] Deploy as an installable PWA
- [ ] Offline queue for shop floor entry

---

## 9. Known problems to fix

- RLS policies still use `auth_role()`, which reads `profiles.role`. The role
  model in `07_roles.sql` supersedes it. Migrate policies to `auth_can()`.
- `profiles.role` is now redundant but still referenced. Keep it until policies
  are migrated, then drop.
- Photo upload was never built against Supabase Storage. When you do: HEIC from
  iPhones cannot be decoded by browsers — use `createImageBitmap` with
  `imageOrientation: 'from-image'` and detect HEIC to give a clear message.
- No offline handling. Kanpur shop floor connectivity is not guaranteed.
- No approval workflow yet; results are final as soon as they are entered.

---

## 10. How to work with Yash

- He drives architecture and will redirect you. Take the redirection.
- He asks good structural questions. Answer them honestly, including when a
  request would be a mistake, and say why.
- Do not hand him five files to move. Do the work in the repo directly.
- When something fails, read the file and find it. He should not be debugging.
- Check your assumptions against the specification, not memory. An earlier
  breaking strength figure was quoted wrong from memory and had to be corrected
  against the source document.
