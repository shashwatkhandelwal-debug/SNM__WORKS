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


async def test_5_1_anon_no_table_or_sequence_privileges(pg_conn):
    """5.1: anon has NO table or sequence privileges in public (0 rows expected)."""
    rows = await pg_conn.fetch('''
        SELECT c.relname, c.relkind
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r','p','v','m','S')
          AND (has_table_privilege('anon', c.oid, 'SELECT')
            OR has_table_privilege('anon', c.oid, 'INSERT')
            OR has_table_privilege('anon', c.oid, 'UPDATE')
            OR has_table_privilege('anon', c.oid, 'DELETE')
            OR has_table_privilege('anon', c.oid, 'TRUNCATE')
            OR has_table_privilege('anon', c.oid, 'REFERENCES')
            OR has_table_privilege('anon', c.oid, 'TRIGGER')
            OR (c.relkind = 'S' AND (has_sequence_privilege('anon', c.oid, 'USAGE')
                                  OR has_sequence_privilege('anon', c.oid, 'SELECT')
                                  OR has_sequence_privilege('anon', c.oid, 'UPDATE'))));
    ''')
    assert len(rows) == 0, f"anon unexpectedly holds table/sequence privileges: {[r['relname'] for r in rows]}"


async def test_5_2_authenticated_no_truncate_references_trigger(pg_conn):
    """5.2: authenticated has NO TRUNCATE, REFERENCES, TRIGGER on any public table (0 rows expected)."""
    rows = await pg_conn.fetch('''
        SELECT c.relname,
               has_table_privilege('authenticated', c.oid, 'TRUNCATE') AS trunc,
               has_table_privilege('authenticated', c.oid, 'REFERENCES') AS refs,
               has_table_privilege('authenticated', c.oid, 'TRIGGER') AS trig
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
          AND (has_table_privilege('authenticated', c.oid, 'TRUNCATE')
            OR has_table_privilege('authenticated', c.oid, 'REFERENCES')
            OR has_table_privilege('authenticated', c.oid, 'TRIGGER'));
    ''')
    assert len(rows) == 0, f"authenticated holds dangerous privileges on: {[r['relname'] for r in rows]}"


async def test_5_3_delete_matches_delete_policies_exactly(pg_conn):
    """5.3: DELETE privilege matches DELETE/ALL policies set-equality (expect 0 mismatched rows)."""
    mismatched = await pg_conn.fetch('''
        SELECT c.relname
        FROM pg_class c
        WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
          AND has_table_privilege('authenticated', c.oid, 'DELETE')
          <> EXISTS (SELECT 1 FROM pg_policies p
                     WHERE p.schemaname = 'public' AND p.tablename = c.relname
                       AND p.cmd IN ('DELETE', 'ALL'));
    ''')
    assert len(mismatched) == 0, f"Mismatches between DELETE privilege and DELETE policies: {[r['relname'] for r in mismatched]}"


async def test_5_4_authenticated_has_select_insert_update_on_all_tables_and_sequences(pg_conn):
    """5.4: authenticated has SELECT, INSERT, UPDATE on every table; USAGE, SELECT on every sequence."""
    missing_tables = await pg_conn.fetch('''
        SELECT c.relname,
               has_table_privilege('authenticated', c.oid, 'SELECT') AS sel,
               has_table_privilege('authenticated', c.oid, 'INSERT') AS ins,
               has_table_privilege('authenticated', c.oid, 'UPDATE') AS upd
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
          AND NOT (has_table_privilege('authenticated', c.oid, 'SELECT')
               AND has_table_privilege('authenticated', c.oid, 'INSERT')
               AND has_table_privilege('authenticated', c.oid, 'UPDATE'));
    ''')
    assert len(missing_tables) == 0, f"authenticated missing SELECT/INSERT/UPDATE on: {[r['relname'] for r in missing_tables]}"

    missing_seqs = await pg_conn.fetch('''
        SELECT c.relname,
               has_sequence_privilege('authenticated', c.oid, 'USAGE') AS usg,
               has_sequence_privilege('authenticated', c.oid, 'SELECT') AS sel
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'S'
          AND NOT (has_sequence_privilege('authenticated', c.oid, 'USAGE')
               AND has_sequence_privilege('authenticated', c.oid, 'SELECT'));
    ''')
    assert len(missing_seqs) == 0, f"authenticated missing USAGE/SELECT on sequences: {[r['relname'] for r in missing_seqs]}"


async def test_5_5_behaviour_checks(pg_conn):
    """5.5: Behaviour checks for privilege barriers."""
    admin_id = '00000000-0000-0000-0000-000000000001'
    claims_json = json.dumps({'sub': admin_id, 'role': 'authenticated'})

    # (a) authenticated TRUNCATE public.audit_log -> SQLSTATE 42501
    async with pg_conn.transaction():
        await pg_conn.execute('SET LOCAL ROLE authenticated;')
        await pg_conn.execute("SELECT set_config('request.jwt.claims', $1, true)", claims_json)
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError) as exc:
            await pg_conn.execute('TRUNCATE TABLE public.audit_log;')
        assert exc.value.sqlstate == '42501'

    # (b) authenticated DELETE FROM public.customers -> SQLSTATE 42501
    async with pg_conn.transaction():
        await pg_conn.execute('SET LOCAL ROLE authenticated;')
        await pg_conn.execute("SELECT set_config('request.jwt.claims', $1, true)", claims_json)
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError) as exc:
            await pg_conn.execute('DELETE FROM public.customers WHERE 1=0;')
        assert exc.value.sqlstate == '42501'

    # (c) authenticated DELETE FROM public.sku_specifications -> succeeds
    async with pg_conn.transaction():
        await pg_conn.execute('SET LOCAL ROLE authenticated;')
        await pg_conn.execute("SELECT set_config('request.jwt.claims', $1, true)", claims_json)
        await pg_conn.execute('DELETE FROM public.sku_specifications WHERE 1=0;')

    # (d) anon SELECT FROM public.masters -> SQLSTATE 42501
    async with pg_conn.transaction():
        await pg_conn.execute('SET LOCAL ROLE anon;')
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError) as exc:
            await pg_conn.fetch('SELECT * FROM public.masters LIMIT 1;')
        assert exc.value.sqlstate == '42501'

    # (e) anon SELECT verify_public_certificate(...) -> succeeds (SECURITY DEFINER runs as owner)
    cert_no = f'SNM-TC-35-{uuid.uuid4().hex[:6]}'
    raw_hash = 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789'
    valid_job_id = await pg_conn.fetchval('''
        INSERT INTO public.jobs (job_no, product, qty_ordered, status, created_by)
        VALUES ($1, 'Tape', 50, 'Planned', $2) RETURNING id;
    ''', f'JOB-35-{uuid.uuid4().hex[:6]}', admin_id)
    await pg_conn.execute('''
        INSERT INTO public.test_certificates (cert_no, job_id, issued_by, issued_at, sha256_hash, status)
        VALUES ($1, $2, $3, now(), $4, 'Issued');
    ''', cert_no, valid_job_id, admin_id, raw_hash)

    async with pg_conn.transaction():
        await pg_conn.execute('SET LOCAL ROLE anon;')
        res = await pg_conn.fetchrow('SELECT * FROM public.verify_public_certificate($1, $2);', cert_no, raw_hash[:32])
        assert res is not None
        assert res['cert_no'] == cert_no

    # (f) postgres superuser TRUNCATE public.audit_log -> succeeds
    async with pg_conn.transaction():
        await pg_conn.execute('TRUNCATE TABLE public.audit_log;')


async def test_5_6_default_privileges_probe_tables_sequences(pg_conn):
    """5.6: Default privileges probe on newly created tables and sequences."""
    tr = pg_conn.transaction()
    await tr.start()
    try:
        await pg_conn.execute('CREATE TABLE public._probe35 (id serial primary key, name text);')
        
        # Check anon on table
        anon_table_privs = await pg_conn.fetchrow('''
            SELECT has_table_privilege('anon', 'public._probe35', 'SELECT') AS sel,
                   has_table_privilege('anon', 'public._probe35', 'INSERT') AS ins,
                   has_table_privilege('anon', 'public._probe35', 'UPDATE') AS upd,
                   has_table_privilege('anon', 'public._probe35', 'DELETE') AS del,
                   has_table_privilege('anon', 'public._probe35', 'TRUNCATE') AS trunc;
        ''')
        assert not any(anon_table_privs.values()), f"anon has privileges on _probe35: {dict(anon_table_privs)}"

        # Check authenticated on table
        auth_table_privs = await pg_conn.fetchrow('''
            SELECT has_table_privilege('authenticated', 'public._probe35', 'SELECT') AS sel,
                   has_table_privilege('authenticated', 'public._probe35', 'INSERT') AS ins,
                   has_table_privilege('authenticated', 'public._probe35', 'UPDATE') AS upd,
                   has_table_privilege('authenticated', 'public._probe35', 'DELETE') AS del,
                   has_table_privilege('authenticated', 'public._probe35', 'TRUNCATE') AS trunc,
                   has_table_privilege('authenticated', 'public._probe35', 'REFERENCES') AS refs,
                   has_table_privilege('authenticated', 'public._probe35', 'TRIGGER') AS trig;
        ''')
        assert auth_table_privs['sel'] is True
        assert auth_table_privs['ins'] is True
        assert auth_table_privs['upd'] is True
        assert auth_table_privs['del'] is False
        assert auth_table_privs['trunc'] is False
        assert auth_table_privs['refs'] is False
        assert auth_table_privs['trig'] is False

        # Check sequence
        auth_seq_privs = await pg_conn.fetchrow('''
            SELECT has_sequence_privilege('authenticated', 'public._probe35_id_seq', 'USAGE') AS usg,
                   has_sequence_privilege('authenticated', 'public._probe35_id_seq', 'SELECT') AS sel,
                   has_sequence_privilege('authenticated', 'public._probe35_id_seq', 'UPDATE') AS upd;
        ''')
        assert auth_seq_privs['usg'] is True
        assert auth_seq_privs['sel'] is True
    finally:
        await tr.rollback()
