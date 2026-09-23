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
    'sql/22_platform_connections.sql',
    'sql/24_variant_approval_workflow.sql',
    'sql/25_materials.sql',
    'sql/26_traceability.sql',
    'sql/28_spec_pdf_ingestion.sql',
    'sql/29_spec_uploads_standalone_and_source.sql',
    'sql/29_tally_automation.sql',
    'sql/30_kpi_public_verify_analytics.sql',
    'sql/30_owner_conflict_exemption.sql',
    'sql/31_trade_documents.sql',
    'sql/33_storage_buckets.sql',
    'sql/34_function_exposure_hardening.sql',
    'sql/35_table_privilege_hardening.sql',
    'sql/36_default_maintain_revoke.sql',
    'sql/37_tasks_read_permission.sql',
    'sql/38_drop_audit_insert_policy.sql',
    'sql/39_drop_unused_pg_net.sql',
    'sql/40_snm_app_noinherit.sql',
    'sql/41_retire_legacy_and_diagnostics.sql',
    'sql/42_production_parity_audit_and_functions.sql',
    'sql/43_production_parity_policies.sql',
    'sql/44_production_parity_spec_tables.sql',
    'sql/45_spec_integrity_standalone_uploads.sql',
    'sql/46_jobs_variant_link.sql',
]

async def apply_all():
    admin = await asyncpg.connect('postgresql://postgres@127.0.0.1:5433/postgres')
    await admin.execute('DROP DATABASE IF EXISTS snm_test_db WITH (FORCE)')
    await admin.execute("CREATE DATABASE snm_test_db WITH ENCODING 'UTF8' LC_COLLATE = 'C' LC_CTYPE = 'C' TEMPLATE template0;")
    await admin.close()

    conn = await asyncpg.connect('postgresql://postgres@127.0.0.1:5433/snm_test_db')
    conn.add_log_listener(lambda c, m: print(f"    NOTICE: {m.message}"))
    
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
    print('\nAll migrations applied cleanly with zero errors!')
    await conn.close()

if __name__ == '__main__':
    asyncio.run(apply_all())
