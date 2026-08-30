import asyncio
import asyncpg

async def inspect_policies():
    conn = await asyncpg.connect('postgresql://postgres@127.0.0.1:5433/snm_test_db')
    
    policies = await conn.fetch('''
        SELECT tablename, policyname, permissive, cmd, qual, with_check
        FROM pg_policies
        WHERE schemaname = 'public'
        ORDER BY tablename, policyname;
    ''')
    
    tables = {}
    for p in policies:
        t = p['tablename']
        if t not in tables:
            tables[t] = []
        tables[t].append(p)
        
    print(f"Total tables with policies: {len(tables)}\n")
    for t, pols in tables.items():
        count = await conn.fetchval(f"SELECT count(*) FROM {t}")
        has_auth_role = any("auth_role" in str(p['qual'] or '') or "auth_role" in str(p['with_check'] or '') for p in pols)
        has_auth_can = any("auth_can" in str(p['qual'] or '') or "auth_can" in str(p['with_check'] or '') for p in pols)
        print(f"TABLE: {t} (row count: {count}) | Uses auth_role: {has_auth_role} | Uses auth_can: {has_auth_can}")
        for p in pols:
            print(f"  * {p['policyname']} [{p['cmd']}]:")
            if p['qual']:
                print(f"      USING: {p['qual']}")
            if p['with_check']:
                print(f"      WITH CHECK: {p['with_check']}")
        print()

    # Also check if any table in public has NO policies
    all_tables = await conn.fetch('''
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name;
    ''')
    no_pol = [r['table_name'] for r in all_tables if r['table_name'] not in tables]
    if no_pol:
        print(f"Tables with NO policies: {no_pol}")

    await conn.close()

if __name__ == '__main__':
    asyncio.run(inspect_policies())
