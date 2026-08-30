-- ============================================================================
-- 11_lab_tests.sql — Lab Tests Specimens, Verdict Generator & Constraints
-- ============================================================================
--
-- DISPOSITION OF EXISTING 01_schema.sql COLUMNS:
--   - 'unit': Kept and actively used for units of measure (e.g. kgf, mm, g/m, %).
--   - 'requirement': Kept as an optional textual requirement/clause reference
--     (or reference to spec_requirements).
--   - 'result': Kept as an optional qualitative summary/notes field.
--     Quantitative analysis and verdict calculations are driven by the new
--     'specimens numeric[]' array.
--
-- CAUTION / PRODUCTION SAFETY NOTICE:
--   - The column additions below use DEFAULT 0 (for spec_value) and DEFAULT false
--     (for is_critical). Furthermore, dropping the existing 'verdict' column and
--     regenerating it as a STORED computed column will recalculate verdicts based
--     on these defaults and current specimens.
--   - In a production environment with existing lab_tests records, an explicit
--     data backfill step is REQUIRED before running this migration against Supabase,
--     otherwise existing verdicts would be overwritten by default calculations.
--
-- NOTE:
--   - sql/10_campaigns.sql and sql/10_storage.sql share prefix 10 from earlier
--     work; flagged for separate cleanup.
-- ============================================================================

-- 1. Add missing limit and specimen columns to lab_tests
ALTER TABLE lab_tests ADD COLUMN IF NOT EXISTS parameter text;
ALTER TABLE lab_tests ADD COLUMN IF NOT EXISTS limit_type limit_kind NOT NULL DEFAULT 'nominal';
ALTER TABLE lab_tests ADD COLUMN IF NOT EXISTS spec_value numeric NOT NULL DEFAULT 0;
ALTER TABLE lab_tests ADD COLUMN IF NOT EXISTS tolerance numeric;
ALTER TABLE lab_tests ADD COLUMN IF NOT EXISTS upper_limit numeric;
ALTER TABLE lab_tests ADD COLUMN IF NOT EXISTS is_critical boolean NOT NULL DEFAULT false;
ALTER TABLE lab_tests ADD COLUMN IF NOT EXISTS specimens numeric[] NOT NULL DEFAULT '{}';
ALTER TABLE lab_tests ADD COLUMN IF NOT EXISTS approved_by uuid REFERENCES profiles(id);
ALTER TABLE lab_tests ADD COLUMN IF NOT EXISTS approved_at timestamptz;

-- 2. Non-negotiable rule 6: A person cannot approve a record they entered
ALTER TABLE lab_tests DROP CONSTRAINT IF EXISTS lab_tests_no_self_approval;
ALTER TABLE lab_tests ADD CONSTRAINT lab_tests_no_self_approval
  CHECK (approved_by IS NULL OR approved_by <> created_by);

-- 3. Stored verdict generator function
CREATE OR REPLACE FUNCTION lab_test_verdict(
  p_limit_type limit_kind,
  p_spec_value numeric,
  p_tolerance numeric,
  p_upper_limit numeric,
  p_is_critical boolean,
  p_specimens numeric[]
)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
  v_len int;
  v_min numeric;
  v_max numeric;
  v_avg numeric;
  v_val numeric;
  v_tol numeric := COALESCE(p_tolerance, 0);
  v_upper numeric := COALESCE(p_upper_limit, p_spec_value);
BEGIN
  -- Empty or NULL specimens array -> 'Pending' (normal initial state)
  IF p_specimens IS NULL OR cardinality(p_specimens) = 0 THEN
    RETURN 'Pending';
  END IF;

  v_len := cardinality(p_specimens);
  v_min := p_specimens[1];
  v_max := p_specimens[1];
  v_avg := 0;

  FOREACH v_val IN ARRAY p_specimens LOOP
    IF v_val IS NULL THEN
      RETURN 'Pending';
    END IF;
    IF v_val < v_min THEN
      v_min := v_val;
    END IF;
    IF v_val > v_max THEN
      v_max := v_val;
    END IF;
    v_avg := v_avg + v_val;
  END LOOP;

  v_avg := v_avg / v_len;

  -- Critical requirements (MIL-W-4088K 3.6.1): min/max boundary on EVERY specimen, NEVER average
  IF p_is_critical THEN
    IF p_limit_type = 'minimum' THEN
      IF v_min >= p_spec_value THEN
        RETURN 'PASS';
      ELSE
        RETURN 'FAIL';
      END IF;
    ELSIF p_limit_type = 'maximum' THEN
      IF v_max <= p_spec_value THEN
        RETURN 'PASS';
      ELSE
        RETURN 'FAIL';
      END IF;
    ELSIF p_limit_type = 'range' THEN
      IF v_min >= p_spec_value AND v_max <= v_upper THEN
        RETURN 'PASS';
      ELSE
        RETURN 'FAIL';
      END IF;
    ELSIF p_limit_type = 'nominal' THEN
      FOREACH v_val IN ARRAY p_specimens LOOP
        IF abs(v_val - p_spec_value) > v_tol THEN
          RETURN 'FAIL';
        END IF;
      END LOOP;
      RETURN 'PASS';
    END IF;
  ELSE
    -- Non-critical requirements: Evaluated against specimen average
    IF p_limit_type = 'minimum' THEN
      IF v_avg >= p_spec_value THEN
        RETURN 'PASS';
      ELSE
        RETURN 'FAIL';
      END IF;
    ELSIF p_limit_type = 'maximum' THEN
      IF v_avg <= p_spec_value THEN
        RETURN 'PASS';
      ELSE
        RETURN 'FAIL';
      END IF;
    ELSIF p_limit_type = 'range' THEN
      IF v_avg >= p_spec_value AND v_avg <= v_upper THEN
        RETURN 'PASS';
      ELSE
        RETURN 'FAIL';
      END IF;
    ELSIF p_limit_type = 'nominal' THEN
      IF abs(v_avg - p_spec_value) <= v_tol THEN
        RETURN 'PASS';
      ELSE
        RETURN 'FAIL';
      END IF;
    END IF;
  END IF;

  RETURN 'FAIL';
END $$;

GRANT EXECUTE ON FUNCTION lab_test_verdict(limit_kind, numeric, numeric, numeric, boolean, numeric[]) TO authenticated, anon;

-- 4. Replace static verdict column with stored generated column
ALTER TABLE lab_tests DROP COLUMN IF EXISTS verdict CASCADE;
ALTER TABLE lab_tests ADD COLUMN verdict text
  GENERATED ALWAYS AS (
    lab_test_verdict(limit_type, spec_value, tolerance, upper_limit, is_critical, specimens)
  ) STORED;

-- 5. Strict RBAC RLS Policies (auth_can only — no legacy role bypass)
DROP POLICY IF EXISTS tests_read ON lab_tests;
DROP POLICY IF EXISTS tests_write ON lab_tests;
DROP POLICY IF EXISTS tests_insert ON lab_tests;
DROP POLICY IF EXISTS tests_update ON lab_tests;

CREATE POLICY tests_read ON lab_tests FOR SELECT
  USING (auth_can('tests', 'read'));

CREATE POLICY tests_insert ON lab_tests FOR INSERT
  WITH CHECK (auth_can('tests', 'create'));

CREATE POLICY tests_update ON lab_tests FOR UPDATE
  USING (auth_can('tests', 'update') OR auth_can('tests', 'approve'));
