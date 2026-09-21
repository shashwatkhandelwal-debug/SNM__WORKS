-- 43_production_parity_policies.sql
--
-- LOCAL PARITY MIGRATION. Replaces the RLS policies of customers, skus and suppliers with exactly the policies running on the
-- Supabase project (read from the project on 2026-09-21). It must NEVER change the project: skipped when
-- supabase_migrations.schema_migrations exists (that schema exists only on the project).
-- Acceptance is objective: after this file, `python tests/drift_compare.py` must show identical `p=` hashes for these three tables.
-- Any local test or feature that relied on a local-only policy (for example a `commercial` or `marketing` permission that the project
-- does not grant) must be REPORTED, not patched: it means the app expects something the project does not enforce.

DO $parity$
DECLARE
    r record;
BEGIN
    IF to_regclass('supabase_migrations.schema_migrations') IS NOT NULL THEN
        RAISE NOTICE '43-skip: Supabase project detected';
        RETURN;
    END IF;

    FOR r IN SELECT tablename, policyname FROM pg_policies
             WHERE schemaname = 'public' AND tablename IN ('customers', 'skus', 'suppliers') LOOP
        EXECUTE format('DROP POLICY %I ON public.%I', r.policyname, r.tablename);
    END LOOP;

    -- customers
    CREATE POLICY customers_insert ON public.customers FOR INSERT TO authenticated
        WITH CHECK (auth_can('customers', 'create'));
    CREATE POLICY customers_select ON public.customers FOR SELECT TO authenticated
        USING (auth_can('customers', 'read'));
    CREATE POLICY customers_tally_read ON public.customers FOR SELECT TO public
        USING (auth_can('tally', 'read'));
    CREATE POLICY customers_tally_update ON public.customers FOR UPDATE TO public
        USING (auth_can('customers', 'update') OR auth_can('tally', 'update'))
        WITH CHECK (auth_can('customers', 'update') OR auth_can('tally', 'update'));
    CREATE POLICY customers_update ON public.customers FOR UPDATE TO authenticated
        USING (auth_can('customers', 'update') OR auth_can('customers', 'approve'));

    -- skus
    CREATE POLICY skus_insert ON public.skus FOR INSERT TO public
        WITH CHECK (auth_can('skus', 'create'));
    CREATE POLICY skus_read ON public.skus FOR SELECT TO public
        USING (auth_can('skus', 'read') OR auth_can('specifications', 'read') OR auth_can('specifications', 'create'));
    CREATE POLICY skus_tally_read ON public.skus FOR SELECT TO public
        USING (auth_can('tally', 'read'));
    CREATE POLICY skus_tally_update ON public.skus FOR UPDATE TO public
        USING (auth_can('skus', 'update') OR auth_can('tally', 'update'))
        WITH CHECK (auth_can('skus', 'update') OR auth_can('tally', 'update'));
    CREATE POLICY skus_update ON public.skus FOR UPDATE TO public
        USING (auth_can('skus', 'update') OR auth_can('specifications', 'create') OR auth_can('specifications', 'update'));

    -- suppliers
    CREATE POLICY suppliers_analytics_select ON public.suppliers FOR SELECT TO authenticated
        USING (auth_can('qc', 'read') OR auth_can('tests', 'read'));
    CREATE POLICY suppliers_insert ON public.suppliers FOR INSERT TO authenticated
        WITH CHECK (auth_can('purchase', 'create') OR auth_can('stock', 'create'));
    CREATE POLICY suppliers_select ON public.suppliers FOR SELECT TO authenticated
        USING (auth_can('purchase', 'read') OR auth_can('stock', 'read'));
    CREATE POLICY suppliers_tally_read ON public.suppliers FOR SELECT TO public
        USING (auth_can('tally', 'read'));
    CREATE POLICY suppliers_tally_update ON public.suppliers FOR UPDATE TO public
        USING (auth_can('stock', 'update') OR auth_can('tally', 'update'))
        WITH CHECK (auth_can('stock', 'update') OR auth_can('tally', 'update'));
    CREATE POLICY suppliers_update ON public.suppliers FOR UPDATE TO authenticated
        USING (auth_can('purchase', 'update') OR auth_can('stock', 'update'));

    RAISE NOTICE '43: parity applied (customers, skus, suppliers policies)';
END
$parity$;
