-- ============================================================================
-- SNM WORKS — GENERIC SPECIFICATION MODEL & INSPECTION PLANS
-- Aligned with Production Supabase Database
-- ============================================================================

CREATE TABLE IF NOT EXISTS specifications (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  spec_no       text NOT NULL,
  revision      text NOT NULL DEFAULT 'R0',
  title         text NOT NULL,
  issuing_body  text,                           -- US Army Natick RD&E Center, BIS, ISO
  issued_on     date,
  supersedes    text,
  distribution  text,
  scope         text,
  notes         text,
  active        boolean NOT NULL DEFAULT true,
  created_by    uuid REFERENCES profiles(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (spec_no, revision)
);

CREATE TABLE IF NOT EXISTS spec_variants (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  spec_id      uuid NOT NULL REFERENCES specifications(id) ON DELETE CASCADE,
  variant_code text NOT NULL,                  -- Type VIII Class 1, etc.
  name         text NOT NULL,
  class        text,
  description  text,
  status       text NOT NULL DEFAULT 'Draft' CHECK (status IN ('Draft', 'Approved', 'Archived')),
  created_by   uuid REFERENCES profiles(id),
  approved_by  uuid REFERENCES profiles(id),
  approved_at  timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (spec_id, variant_code),
  CONSTRAINT spec_variants_no_self_approval CHECK (approved_by IS NULL OR approved_by <> created_by)
);

CREATE TABLE IF NOT EXISTS spec_requirements (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  spec_id      uuid NOT NULL REFERENCES specifications(id) ON DELETE CASCADE,
  variant_id   uuid REFERENCES spec_variants(id) ON DELETE CASCADE,
  parameter    text NOT NULL,
  unit         text,
  limit_type   text NOT NULL DEFAULT 'nominal', -- nominal | minimum | maximum | range | text
  spec_value   numeric,
  tolerance    numeric,
  upper_limit  numeric,
  text_value   text,
  test_method  text,
  clause_ref   text,
  is_critical  boolean NOT NULL DEFAULT false,
  sort_order   int NOT NULL DEFAULT 0,
  notes        text,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS spec_defects (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  spec_id        uuid NOT NULL REFERENCES specifications(id) ON DELETE CASCADE,
  examine        text NOT NULL,
  defect         text NOT NULL,
  classification text NOT NULL DEFAULT 'Major' CHECK (classification IN ('Major', 'Minor')),
  clause_ref     text,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS spec_sampling (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  spec_id       uuid NOT NULL REFERENCES specifications(id) ON DELETE CASCADE,
  basis         text NOT NULL,                  -- yards, units, etc.
  purpose       text NOT NULL,                  -- visual, dimensional, testing
  lot_from      numeric NOT NULL,
  lot_to        numeric,                        -- NULL means 'and above'
  sample_size   numeric NOT NULL,
  accept_number int,                            -- NULL if not applicable/tracked
  notes         text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- spec_check_plan function
CREATE OR REPLACE FUNCTION spec_check_plan(p_variant uuid)
RETURNS TABLE(
  parameter    text,
  unit         text,
  limit_type   text,
  spec_value   numeric,
  tolerance    numeric,
  upper_limit  numeric,
  text_value   text,
  test_method  text,
  clause_ref   text,
  is_critical  boolean
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path TO 'public'
AS $$
  SELECT r.parameter, r.unit, r.limit_type, r.spec_value, r.tolerance,
         r.upper_limit, r.text_value, r.test_method, r.clause_ref, r.is_critical
  FROM spec_requirements r
  JOIN spec_variants v ON v.id = p_variant
  WHERE r.spec_id = v.spec_id
    AND (r.variant_id = v.id OR r.variant_id IS NULL)
    AND v.status = 'Approved'
  ORDER BY r.sort_order, r.parameter;
$$;

GRANT EXECUTE ON FUNCTION spec_check_plan(uuid) TO authenticated;

-- RLS Configuration
ALTER TABLE specifications    ENABLE ROW LEVEL SECURITY;
ALTER TABLE spec_variants     ENABLE ROW LEVEL SECURITY;
ALTER TABLE spec_requirements ENABLE ROW LEVEL SECURITY;
ALTER TABLE spec_defects      ENABLE ROW LEVEL SECURITY;
ALTER TABLE spec_sampling     ENABLE ROW LEVEL SECURITY;

CREATE POLICY specs_read ON specifications FOR SELECT USING (true);
CREATE POLICY variants_read ON spec_variants FOR SELECT USING (true);
CREATE POLICY reqs_read ON spec_requirements FOR SELECT USING (true);
CREATE POLICY defects_read ON spec_defects FOR SELECT USING (true);
CREATE POLICY sampling_read ON spec_sampling FOR SELECT USING (true);
