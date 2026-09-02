-- ============================================================================
-- SNM WORKS - SPECIFICATION VARIANTS APPROVAL WORKFLOW & SEGREGATION OF DUTIES
-- Migration 24
-- ============================================================================

-- 1. Add status, created_by, approved_by to spec_variants
ALTER TABLE spec_variants 
  ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'Draft' CHECK (status IN ('Draft', 'Approved', 'Archived')),
  ADD COLUMN IF NOT EXISTS created_by uuid REFERENCES profiles(id),
  ADD COLUMN IF NOT EXISTS approved_by uuid REFERENCES profiles(id),
  ADD COLUMN IF NOT EXISTS approved_at timestamptz;

-- 2. Add four-eyes self-approval constraint (approved_by <> created_by)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'spec_variants_no_self_approval'
  ) THEN
    ALTER TABLE spec_variants 
      ADD CONSTRAINT spec_variants_no_self_approval 
      CHECK (approved_by IS NULL OR approved_by <> created_by);
  END IF;
END $$;

-- 3. Update spec_check_plan to gate on approved variants
CREATE OR REPLACE FUNCTION public.spec_check_plan(p_variant uuid)
 RETURNS TABLE(
   parameter text,
   unit text,
   limit_type text,
   spec_value numeric,
   tolerance numeric,
   upper_limit numeric,
   text_value text,
   test_method text,
   clause_ref text,
   is_critical boolean
 )
 LANGUAGE sql
 STABLE
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
  SELECT r.parameter, r.unit, r.limit_type, r.spec_value, r.tolerance,
         r.upper_limit, r.text_value, r.test_method, r.clause_ref, r.is_critical
  FROM spec_requirements r
  JOIN spec_variants v ON v.id = p_variant
  WHERE r.spec_id = v.spec_id
    AND (r.variant_id = v.id OR r.variant_id IS NULL)
    AND v.status = 'Approved'
  ORDER BY r.sort_order, r.parameter;
$function$;

GRANT EXECUTE ON FUNCTION spec_check_plan(uuid) TO authenticated;
