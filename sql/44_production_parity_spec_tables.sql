-- 44_production_parity_spec_tables.sql
--
-- Production parity for specification tables:
--   specifications, spec_variants, spec_requirements, spec_sampling, spec_defects
--
-- Guards:
--   * skipped when running against Supabase project (schema_migrations table exists)
--   * skipped when trigger audit_specs already exists on public.specifications

DO $$
BEGIN
    IF to_regclass('supabase_migrations.schema_migrations') IS NOT NULL THEN
        RAISE NOTICE '44-skip: running against Supabase project, parity already present';
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_trigger g
        JOIN pg_class c ON c.oid = g.tgrelid
        WHERE c.relname = 'specifications' AND c.relnamespace = 'public'::regnamespace AND g.tgname = 'audit_specs'
    ) THEN
        RAISE NOTICE '44-skip: parity already applied (audit_specs trigger exists)';
        RETURN;
    END IF;

    -- =========================================================================
    -- 1. specifications
    -- =========================================================================
    ALTER TABLE public.specifications ALTER COLUMN revision DROP NOT NULL;
    ALTER TABLE public.specifications ALTER COLUMN revision DROP DEFAULT;

    DROP TRIGGER IF EXISTS audit_specs ON public.specifications;
    CREATE TRIGGER audit_specs AFTER INSERT OR DELETE OR UPDATE ON public.specifications FOR EACH ROW EXECUTE FUNCTION log_change();

    DROP TRIGGER IF EXISTS touch_specs ON public.specifications;
    CREATE TRIGGER touch_specs BEFORE UPDATE ON public.specifications FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

    DROP POLICY IF EXISTS specs_insert ON public.specifications;
    DROP POLICY IF EXISTS specs_select ON public.specifications;
    DROP POLICY IF EXISTS specs_update ON public.specifications;
    DROP POLICY IF EXISTS specifications_insert ON public.specifications;
    DROP POLICY IF EXISTS specifications_select ON public.specifications;
    DROP POLICY IF EXISTS specifications_update ON public.specifications;

    CREATE POLICY specifications_insert ON public.specifications FOR INSERT TO authenticated WITH CHECK (auth_can('specifications'::text, 'create'::text));
    CREATE POLICY specifications_select ON public.specifications FOR SELECT TO authenticated USING (auth_can('specifications'::text, 'read'::text));
    CREATE POLICY specifications_update ON public.specifications FOR UPDATE TO authenticated USING (auth_can('specifications'::text, 'update'::text) OR auth_can('specifications'::text, 'approve'::text));

    -- =========================================================================
    -- 2. spec_variants
    -- =========================================================================
    ALTER TABLE public.spec_variants DROP CONSTRAINT IF EXISTS spec_variants_spec_id_variant_code_key;
    ALTER TABLE public.spec_variants DROP COLUMN IF EXISTS variant_code;
    ALTER TABLE public.spec_variants DROP COLUMN IF EXISTS name;
    ALTER TABLE public.spec_variants DROP COLUMN IF EXISTS created_at;

    IF EXISTS (SELECT 1 FROM public.spec_variants WHERE designation IS NULL) THEN
        RAISE EXCEPTION '44: NULL designation rows exist';
    END IF;
    ALTER TABLE public.spec_variants ALTER COLUMN designation SET NOT NULL;

    ALTER TABLE public.spec_variants DROP CONSTRAINT IF EXISTS spec_variants_spec_id_designation_class_key;
    ALTER TABLE public.spec_variants ADD CONSTRAINT spec_variants_spec_id_designation_class_key UNIQUE (spec_id, designation, class);

    DROP POLICY IF EXISTS variants_insert ON public.spec_variants;
    DROP POLICY IF EXISTS variants_select ON public.spec_variants;
    DROP POLICY IF EXISTS variants_update ON public.spec_variants;
    DROP POLICY IF EXISTS spec_variants_insert ON public.spec_variants;
    DROP POLICY IF EXISTS spec_variants_select ON public.spec_variants;
    DROP POLICY IF EXISTS spec_variants_update ON public.spec_variants;

    CREATE POLICY spec_variants_insert ON public.spec_variants FOR INSERT TO authenticated WITH CHECK (auth_can('specifications'::text, 'create'::text));
    CREATE POLICY spec_variants_select ON public.spec_variants FOR SELECT TO authenticated USING (auth_can('specifications'::text, 'read'::text));
    CREATE POLICY spec_variants_update ON public.spec_variants FOR UPDATE TO authenticated USING (auth_can('specifications'::text, 'update'::text) OR auth_can('specifications'::text, 'approve'::text));

    -- =========================================================================
    -- 3. spec_requirements
    -- =========================================================================
    ALTER TABLE public.spec_requirements DROP COLUMN IF EXISTS created_at;

    ALTER TABLE public.spec_requirements DROP CONSTRAINT IF EXISTS spec_requirements_limit_type_check;
    ALTER TABLE public.spec_requirements ADD CONSTRAINT spec_requirements_limit_type_check CHECK (limit_type = ANY (ARRAY['nominal'::text, 'minimum'::text, 'maximum'::text, 'range'::text, 'text'::text]));

    DROP INDEX IF EXISTS public.req_spec_idx;
    CREATE INDEX req_spec_idx ON public.spec_requirements USING btree (spec_id);

    DROP INDEX IF EXISTS public.req_variant_idx;
    CREATE INDEX req_variant_idx ON public.spec_requirements USING btree (variant_id);

    DROP TRIGGER IF EXISTS audit_spec_reqs ON public.spec_requirements;
    CREATE TRIGGER audit_spec_reqs AFTER INSERT OR DELETE OR UPDATE ON public.spec_requirements FOR EACH ROW EXECUTE FUNCTION log_change();

    DROP POLICY IF EXISTS reqs_insert ON public.spec_requirements;
    DROP POLICY IF EXISTS reqs_select ON public.spec_requirements;
    DROP POLICY IF EXISTS reqs_update ON public.spec_requirements;
    DROP POLICY IF EXISTS spec_requirements_insert ON public.spec_requirements;
    DROP POLICY IF EXISTS spec_requirements_select ON public.spec_requirements;
    DROP POLICY IF EXISTS spec_requirements_update ON public.spec_requirements;

    CREATE POLICY spec_requirements_insert ON public.spec_requirements FOR INSERT TO authenticated WITH CHECK (auth_can('specifications'::text, 'create'::text));
    CREATE POLICY spec_requirements_select ON public.spec_requirements FOR SELECT TO authenticated USING (auth_can('specifications'::text, 'read'::text));
    CREATE POLICY spec_requirements_update ON public.spec_requirements FOR UPDATE TO authenticated USING (auth_can('specifications'::text, 'update'::text) OR auth_can('specifications'::text, 'approve'::text));

    -- =========================================================================
    -- 4. spec_sampling
    -- =========================================================================
    ALTER TABLE public.spec_sampling ALTER COLUMN purpose DROP NOT NULL;
    ALTER TABLE public.spec_sampling ALTER COLUMN lot_from SET DEFAULT 0;
    ALTER TABLE public.spec_sampling DROP COLUMN IF EXISTS created_at;

    DROP POLICY IF EXISTS sampling_insert ON public.spec_sampling;
    DROP POLICY IF EXISTS sampling_select ON public.spec_sampling;
    DROP POLICY IF EXISTS sampling_update ON public.spec_sampling;
    DROP POLICY IF EXISTS spec_sampling_insert ON public.spec_sampling;
    DROP POLICY IF EXISTS spec_sampling_select ON public.spec_sampling;
    DROP POLICY IF EXISTS spec_sampling_update ON public.spec_sampling;

    CREATE POLICY spec_sampling_insert ON public.spec_sampling FOR INSERT TO authenticated WITH CHECK (auth_can('specifications'::text, 'create'::text));
    CREATE POLICY spec_sampling_select ON public.spec_sampling FOR SELECT TO authenticated USING (auth_can('specifications'::text, 'read'::text));
    CREATE POLICY spec_sampling_update ON public.spec_sampling FOR UPDATE TO authenticated USING (auth_can('specifications'::text, 'update'::text) OR auth_can('specifications'::text, 'approve'::text));

    -- =========================================================================
    -- 5. spec_defects
    -- =========================================================================
    ALTER TABLE public.spec_defects ALTER COLUMN classification DROP DEFAULT;
    ALTER TABLE public.spec_defects DROP COLUMN IF EXISTS created_at;

    DROP POLICY IF EXISTS defects_insert ON public.spec_defects;
    DROP POLICY IF EXISTS defects_select ON public.spec_defects;
    DROP POLICY IF EXISTS defects_update ON public.spec_defects;
    DROP POLICY IF EXISTS spec_defects_insert ON public.spec_defects;
    DROP POLICY IF EXISTS spec_defects_select ON public.spec_defects;
    DROP POLICY IF EXISTS spec_defects_update ON public.spec_defects;

    CREATE POLICY spec_defects_insert ON public.spec_defects FOR INSERT TO authenticated WITH CHECK (auth_can('specifications'::text, 'create'::text));
    CREATE POLICY spec_defects_select ON public.spec_defects FOR SELECT TO authenticated USING (auth_can('specifications'::text, 'read'::text));
    CREATE POLICY spec_defects_update ON public.spec_defects FOR UPDATE TO authenticated USING (auth_can('specifications'::text, 'update'::text) OR auth_can('specifications'::text, 'approve'::text));

    RAISE NOTICE '44: parity applied (spec tables)';
END $$;
