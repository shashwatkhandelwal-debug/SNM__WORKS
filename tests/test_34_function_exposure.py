import json
import uuid
import asyncpg
import pytest
from tests.conftest import LOCAL_TEST_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def pg_conn():
    conn = await asyncpg.connect(LOCAL_TEST_DATABASE_URL)
    yield conn
    await conn.close()


async def test_2_1_anon_no_execute_on_rpcs(pg_conn):
    """2.1: anon has NO execute on the 13 RPCs (0 rows expected)."""
    rows = await pg_conn.fetch('''
        SELECT p.oid::regprocedure::text AS fn
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname IN ('audit_daily_root','auth_role','get_user_role_conflicts','has_role',
                            'job_on_hold','job_traceability','my_roles','my_tasks','rls_report',
                            'security_posture','spec_check_plan','verify_audit_chain','verify_spec_pdf_integrity')
          AND has_function_privilege('anon', p.oid, 'EXECUTE');
    ''')
    assert len(rows) == 0, f"anon unexpectedly has execute on: {[r['fn'] for r in rows]}"


async def test_2_2_anon_keeps_auth_can_and_verify_public_certificate(pg_conn):
    """2.2: anon keeps auth_can and verify_public_certificate (2 rows expected)."""
    rows = await pg_conn.fetch('''
        SELECT p.oid::regprocedure::text AS fn
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname IN ('auth_can','verify_public_certificate')
          AND has_function_privilege('anon', p.oid, 'EXECUTE')
        ORDER BY 1;
    ''')
    fns = [r['fn'] for r in rows]
    assert len(rows) == 2, f"Expected 2 fns for anon, got: {fns}"


async def test_2_3_authenticated_no_execute_on_triggers(pg_conn):
    """2.3: authenticated has NO execute on trigger functions (0 rows expected)."""
    rows = await pg_conn.fetch('''
        SELECT p.oid::regprocedure::text AS fn
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname IN ('handle_new_user','log_change','on_qc_fail','on_test_fail',
                            'chain_audit_row','chain_audit_hash','check_despatch_qc_hold','check_user_role_conflicts',
                            'freeze_approved_costing','process_job_material_issue','touch_updated_at','validate_yarn_lot_release')
          AND has_function_privilege('authenticated', p.oid, 'EXECUTE');
    ''')
    assert len(rows) == 0, f"authenticated unexpectedly has execute on triggers: {[r['fn'] for r in rows]}"


async def test_2_4_authenticated_keeps_rpcs_and_open_fns(pg_conn):
    """2.4: authenticated keeps active business RPCs (9 rows expected after 41 retirement)."""
    rows = await pg_conn.fetch('''
        SELECT p.oid::regprocedure::text AS fn
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname IN ('auth_can','my_roles','my_tasks','job_on_hold','job_traceability',
                            'get_user_role_conflicts','spec_check_plan','verify_spec_pdf_integrity','verify_public_certificate')
          AND has_function_privilege('authenticated', p.oid, 'EXECUTE')
        ORDER BY 1;
    ''')
    assert len(rows) == 9, f"Expected 9 functions for authenticated, got {len(rows)}: {[r['fn'] for r in rows]}"


async def test_2_5_search_path_pinned_on_the_8(pg_conn):
    """2.5: search_path pinned on the 8 target functions."""
    rows = await pg_conn.fetch('''
        SELECT p.oid::regprocedure::text AS fn, p.proconfig
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname IN ('qc_verdict','lab_test_verdict','touch_updated_at','check_despatch_qc_hold','freeze_approved_costing',
                            'process_job_material_issue','validate_yarn_lot_release','check_user_role_conflicts')
          AND p.proconfig IS NOT NULL AND p.proconfig::text ILIKE '%search_path%'
        ORDER BY 1;
    ''')
    assert len(rows) == 8, f"Expected 8 functions with search_path pinned, got {len(rows)}"


async def test_2_6_default_privileges_probe(pg_conn):
    """2.6: default privileges probe in transaction (anon_can=False, auth_can=False)."""
    tr = pg_conn.transaction()
    await tr.start()
    try:
        await pg_conn.execute("CREATE FUNCTION public._probe() RETURNS int LANGUAGE sql AS 'SELECT 1';")
        r = await pg_conn.fetchrow('''
            SELECT has_function_privilege('anon','public._probe()','EXECUTE') AS anon_can,
                   has_function_privilege('authenticated','public._probe()','EXECUTE') AS auth_can;
        ''')
        assert r['anon_can'] is False, "anon should not have execute on newly created function by default"
        assert r['auth_can'] is False, "authenticated should not have execute on newly created function by default"
    finally:
        await tr.rollback()


async def test_3_1_anon_refused_on_security_posture(pg_conn):
    """3.1: anon is refused on revoked security_posture() (SQLSTATE 42501)."""
    async with pg_conn.transaction():
        await pg_conn.execute('SET LOCAL ROLE anon;')
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError) as exc_info:
            await pg_conn.fetch('SELECT * FROM public.security_posture();')
        assert exc_info.value.sqlstate == '42501'


async def test_3_2_anon_masters_behavior(pg_conn):
    """3.2: anon query on public.masters (level-aware: 0 rows under 34 only, 42501 under 34+35)."""
    anon_can_select = await pg_conn.fetchval("SELECT has_table_privilege('anon', 'public.masters', 'SELECT');")
    async with pg_conn.transaction():
        await pg_conn.execute('SET LOCAL ROLE anon;')
        if anon_can_select:
            # 34 only: table privileges remain on masters, RLS policies evaluate auth_can and return 0 rows
            cnt = await pg_conn.fetchval('SELECT count(*) FROM public.masters;')
            assert cnt == 0
        else:
            # 34 + 35: table privileges are revoked, resulting in SQLSTATE 42501
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError) as exc_info:
                await pg_conn.fetchval('SELECT count(*) FROM public.masters;')
            assert exc_info.value.sqlstate == '42501'


async def test_3_3_authenticated_reads_through_rls_and_my_roles(pg_conn):
    """3.3: authenticated with seeded admin JWT reads through RLS and my_roles() returns roles."""
    admin_id = '00000000-0000-0000-0000-000000000001'
    claims_json = json.dumps({'sub': admin_id, 'role': 'authenticated'})
    async with pg_conn.transaction():
        await pg_conn.execute('SET LOCAL ROLE authenticated;')
        await pg_conn.execute("SELECT set_config('request.jwt.claims', $1, true)", claims_json)
        masters_cnt = await pg_conn.fetchval('SELECT count(*) FROM public.masters;')
        assert masters_cnt >= 0
        roles_val = await pg_conn.fetch('SELECT public.my_roles();')
        assert len(roles_val) > 0


async def test_3_4_triggers_fire_as_authenticated(pg_conn):
    """3.4: triggers fire when authenticated performs operations."""
    admin_id = '00000000-0000-0000-0000-000000000001'
    claims_json = json.dumps({'sub': admin_id, 'role': 'authenticated'})
    async with pg_conn.transaction():
        await pg_conn.execute('SET LOCAL ROLE authenticated;')
        await pg_conn.execute("SELECT set_config('request.jwt.claims', $1, true)", claims_json)

        # (a) touch_updated_at trigger on jobs
        job_no = f'JOB-34-{uuid.uuid4().hex[:6]}'
        job_id = await pg_conn.fetchval('''
            INSERT INTO public.jobs (job_no, product, qty_ordered, status, created_by, updated_at)
            VALUES ($1, 'Webbing', 100, 'Planned', $2, now() - interval '10 seconds') RETURNING id;
        ''', job_no, admin_id)
        job_row1 = await pg_conn.fetchrow('SELECT updated_at FROM public.jobs WHERE id = $1', job_id)
        await pg_conn.execute('UPDATE public.jobs SET qty_ordered = 150 WHERE id = $1', job_id)
        job_row2 = await pg_conn.fetchrow('SELECT updated_at FROM public.jobs WHERE id = $1', job_id)
        assert job_row2['updated_at'] > job_row1['updated_at']

        # (b) qc_checks & lab_tests insert with generated verdict
        qc_row = await pg_conn.fetchrow('''
            INSERT INTO public.qc_checks (
                check_no, job_id, stage, parameter, limit_type, spec_value, tolerance, actual, inspector_id
            ) VALUES (
                $1, $2, 'first_piece', 'width', 'nominal', 100.0, 5.0, 102.0, $3
            ) RETURNING id, verdict;
        ''', f'QC-34-{uuid.uuid4().hex[:6]}', job_id, admin_id)
        assert qc_row['verdict'] == 'PASS'

        lab_row = await pg_conn.fetchrow('''
            INSERT INTO public.lab_tests (
                test_id, job_id, test_type, parameter, limit_type, spec_value, is_critical, specimens, created_by
            ) VALUES (
                $1, $2, 'physical', 'breaking_strength', 'minimum', 1000.0, true, ARRAY[1050.0, 1100.0, 1020.0], $3
            ) RETURNING id, verdict;
        ''', f'LT-34-{uuid.uuid4().hex[:6]}', job_id, admin_id)
        assert lab_row['verdict'] == 'PASS'

        # (c) log_change trigger -> audit_log row
        audit_row = await pg_conn.fetchrow('''
            SELECT id, entity, action, row_hash FROM public.audit_log
            WHERE entity = 'jobs' AND entity_ref = $1
            ORDER BY id DESC LIMIT 1;
        ''', job_no)
        assert audit_row is not None
        assert audit_row['entity'] == 'jobs'
        assert audit_row['row_hash'] is not None

        # (d) validate_yarn_lot_release: rejection without pass test
        lot_no = f'LOT-34-{uuid.uuid4().hex[:6]}'
        lot_id = await pg_conn.fetchval('''
            INSERT INTO public.yarn_lots (lot_no, supplier_name, yarn_type, denier, qty_received, qc_status, created_by)
            VALUES ($1, 'Supplier X', 'Nylon', 840, 100, 'Quarantine', $2)
            RETURNING id;
        ''', lot_no, admin_id)

        tester_id = '55555555-5555-5555-5555-555555555555'   # lab analyst
        approver_id = '44444444-4444-4444-4444-444444444444' # chief quality

        # Negative path: tested_by and released_by are set, but no PASS lab test exists yet
        sp = pg_conn.transaction()
        await sp.start()
        try:
            with pytest.raises(asyncpg.exceptions.RaiseError) as exc_info:
                await pg_conn.execute('''
                    UPDATE public.yarn_lots
                    SET qc_status = 'Approved',
                        tested_by = $2,
                        released_by = $3,
                        released_at = now()
                    WHERE id = $1;
                ''', lot_id, tester_id, approver_id)
            assert 'at least one pass lab test is required' in str(exc_info.value).lower()
        finally:
            await sp.rollback()

        # Positive path: link a passing lab test, set tested_by and released_by (distinct)
        await pg_conn.execute('''
            INSERT INTO public.lab_tests (
                test_id, yarn_lot_id, test_type, parameter, limit_type, spec_value, is_critical, specimens, created_by
            ) VALUES (
                $1, $2, 'physical', 'denier', 'nominal', 840.0, false, ARRAY[840.0], $3
            );
        ''', f'LT-LOT-{uuid.uuid4().hex[:6]}', lot_id, tester_id)

        await pg_conn.execute('''
            UPDATE public.yarn_lots
            SET qc_status = 'Approved',
                tested_by = $2,
                released_by = $3,
                released_at = now()
            WHERE id = $1;
        ''', lot_id, tester_id, approver_id)

        updated_lot = await pg_conn.fetchrow('SELECT qc_status, released_by FROM public.yarn_lots WHERE id = $1', lot_id)
        assert updated_lot['qc_status'] == 'Approved'
        assert str(updated_lot['released_by']) == approver_id


async def test_3_5_verify_audit_chain(pg_conn):
    """3.5: verify_audit_chain() returns status OK/VALID."""
    chain_res = await pg_conn.fetch('SELECT * FROM public.verify_audit_chain();')
    assert len(chain_res) > 0
    for r in chain_res:
        assert r['status'] in ('OK', 'VALID')


async def test_3_6_verify_public_certificate_as_anon(pg_conn):
    """3.6: verify_public_certificate() as anon works for valid cert and returns null for invalid."""
    admin_id = '00000000-0000-0000-0000-000000000001'
    cert_no = f'SNM-TC-34-{uuid.uuid4().hex[:6]}'
    raw_hash = 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789'
    hash_frag_good = raw_hash[:32]
    hash_frag_bad = '00000000000000000000000000000000'
    valid_job_id = await pg_conn.fetchval('''
        INSERT INTO public.jobs (job_no, product, qty_ordered, status, created_by)
        VALUES ($1, 'Tape', 50, 'Planned', $2) RETURNING id;
    ''', f'JOB-TC-{uuid.uuid4().hex[:6]}', admin_id)

    await pg_conn.execute('''
        INSERT INTO public.test_certificates (
            cert_no, job_id, issued_by, issued_at, sha256_hash, status
        ) VALUES (
            $1, $2, $3, now(), $4, 'Issued'
        );
    ''', cert_no, valid_job_id, admin_id, raw_hash)

    async with pg_conn.transaction():
        await pg_conn.execute('SET LOCAL ROLE anon;')
        good_res = await pg_conn.fetchrow('SELECT * FROM public.verify_public_certificate($1, $2);', cert_no, hash_frag_good)
        bad_res = await pg_conn.fetchrow('SELECT * FROM public.verify_public_certificate($1, $2);', cert_no, hash_frag_bad)
        assert good_res is not None
        assert good_res['cert_no'] == cert_no
        assert good_res['status'] == 'Issued'
        assert bad_res is None
