import asyncio
import asyncpg

sql_files = [
    'tests/local_stubs.sql',
    'sql/01_schema.sql',
    'sql/02_audit_chain.sql',
    'sql/03_security_report.sql',
    'sql/04_reference_data.sql',
    'sql/05_specifications.sql',
    'sql/06_load_mil4088.sql',
    'sql/07_roles.sql',
    'sql/08_lockdown.sql',
    'sql/09_marketing.sql',
    'sql/10a_campaigns.sql',
    'sql/10b_storage.sql',
    'sql/11_lab_tests.sql',
    'sql/12_rls_batch1_active_modules.sql',
    'sql/13_capa.sql',
    'sql/14_constructions.sql',
    'sql/15_dye_recipes.sql',
    'sql/16_downtime.sql',
    'sql/17_despatch.sql',
    'sql/18_organisation.sql',
    'sql/19_costing.sql',
    'sql/20_test_certificates.sql',
    'sql/21_rls_batch2.sql',
]

async def apply_all():
    admin = await asyncpg.connect('postgresql://postgres@127.0.0.1:5433/postgres')
    await admin.execute('DROP DATABASE IF EXISTS snm_test_db WITH (FORCE)')
    await admin.execute("CREATE DATABASE snm_test_db WITH ENCODING 'UTF8' LC_COLLATE = 'C' LC_CTYPE = 'C' TEMPLATE template0;")
    await admin.close()

    conn = await asyncpg.connect('postgresql://postgres@127.0.0.1:5433/snm_test_db')
    
    owner_id = '00000000-0000-0000-0000-000000000001'
    
    for f in sql_files:
        print(f'Applying {f}...')
        with open(f, 'r', encoding='utf-8') as fp:
            content = fp.read()
        
        if '03_security_report.sql' in f:
            # Create owner in auth.users and promote in profiles
            await conn.execute(f"""
                INSERT INTO auth.users (id, email) VALUES ('{owner_id}', 'yashkhandelwal95@gmail.com') ON CONFLICT (id) DO NOTHING;
                UPDATE profiles SET role = 'owner', full_name = 'Yash Khandelwal', active = true WHERE id = '{owner_id}';
                SELECT set_config('request.jwt.claims', '{{"sub":"{owner_id}", "role":"authenticated"}}', false);
            """)
            
        await conn.execute(content)
        print(f'  SUCCESS: {f} applied cleanly')

    # Grant authenticated privileges across all created tables
    await conn.execute("""
        GRANT USAGE ON SCHEMA public TO authenticated, anon;
        GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO authenticated;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO authenticated;
        GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO authenticated;
    """)

    print('\nAll migrations applied cleanly with zero errors!')
    await conn.close()

if __name__ == '__main__':
    asyncio.run(apply_all())
