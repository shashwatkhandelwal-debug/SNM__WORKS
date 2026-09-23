-- 46_jobs_variant_link.sql
-- Link production Job Cards to spec_variants for persistent inspection plan bindings.
-- NOTE: This migration IS safe to apply to production (additive, nullable column, no data loss).

DO $mig$
BEGIN
    IF to_regclass('supabase_migrations.schema_migrations') IS NOT NULL THEN
        RAISE NOTICE '46-skip: Supabase project detected';
        RETURN;
    END IF;

    ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS variant_id uuid REFERENCES public.spec_variants(id) ON DELETE SET NULL;
    CREATE INDEX IF NOT EXISTS idx_jobs_variant_id ON public.jobs(variant_id);
END $mig$;
