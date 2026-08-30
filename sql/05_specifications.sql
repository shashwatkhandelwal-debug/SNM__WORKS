-- ============================================================================
-- SNM WORKS — GENERIC SPECIFICATION MODEL & INSPECTION PLANS
-- ============================================================================

CREATE TABLE IF NOT EXISTS specifications (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  spec_no     text UNIQUE NOT NULL,
  title       text NOT NULL,
  authority   text,                           -- US DoD, BIS, ISO, Customer
  revision    text NOT NULL DEFAULT 'R0',
  status      text NOT NULL DEFAULT 'Active',
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS spec_variants (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  spec_id      uuid NOT NULL REFERENCES specifications(id) ON DELETE CASCADE,
  variant_code text NOT NULL,                  -- Type VIII Class 1, etc.
  name         text NOT NULL,
  class        text,
  description  text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (spec_id, variant_code)
);

CREATE TABLE IF NOT EXISTS spec_requirements (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  variant_id   uuid NOT NULL REFERENCES spec_variants(id) ON DELETE CASCADE,
  stage        text NOT NULL DEFAULT 'On-Loom Inspection',
  parameter    text NOT NULL,
  limit_type   limit_kind NOT NULL DEFAULT 'nominal',
  spec_value   numeric NOT NULL,
  tolerance    numeric,
  upper_limit  numeric,
  unit         text,
  method       text,
  clause       text,
  is_critical  boolean NOT NULL DEFAULT false,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS spec_defects (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  variant_id     uuid NOT NULL REFERENCES spec_variants(id) ON DELETE CASCADE,
  defect_code    text NOT NULL,
  classification text NOT NULL DEFAULT 'Major', -- Critical | Major | Minor
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS spec_sampling (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  variant_id   uuid NOT NULL REFERENCES spec_variants(id) ON DELETE CASCADE,
  lot_size_min int NOT NULL,
  lot_size_max int NOT NULL,
  sample_size  int NOT NULL,
  accept_limit int NOT NULL DEFAULT 0,
  reject_limit int NOT NULL DEFAULT 1,
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- spec_check_plan function
CREATE OR REPLACE FUNCTION spec_check_plan(p_variant_id uuid)
RETURNS TABLE (
  requirement_id uuid,
  stage          text,
  parameter      text,
  limit_type     limit_kind,
  spec_value     numeric,
  tolerance      numeric,
  upper_limit    numeric,
  unit           text,
  method         text,
  is_critical    boolean
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public AS $$
  SELECT
    id,
    stage,
    parameter,
    limit_type,
    spec_value,
    tolerance,
    upper_limit,
    unit,
    method,
    is_critical
  FROM spec_requirements
  WHERE variant_id = p_variant_id
  ORDER BY stage, parameter;
$$;

GRANT EXECUTE ON FUNCTION spec_check_plan(uuid) TO authenticated;

-- RLS
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
