-- ============================================================================
-- SNM WORKS — KPI ANALYTICS, IMMUTABLE SNAPSHOTS & PUBLIC CERTIFICATE VERIFY
-- Migration 30
-- ============================================================================
-- 1. Creates kpi_report_seq sequence and kpi_report_snapshots table.
-- 2. Registers audit trigger trg_audit_kpi_report_snapshots calling log_change().
-- 3. Enables and forces Row Level Security (RLS) on kpi_report_snapshots.
-- 4. Creates verify_public_certificate() SECURITY DEFINER function with
--    exact 32-character hash-fragment validation (fail-closed, zero commercial leakage).
-- 5. Grants permissions to authenticated, anon, and snm_app roles.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. KPI Report Snapshots Table & Sequence
-- ---------------------------------------------------------------------------

CREATE SEQUENCE IF NOT EXISTS kpi_report_seq START WITH 1 INCREMENT BY 1;

CREATE TABLE IF NOT EXISTS kpi_report_snapshots (
  id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  report_no                 text NOT NULL UNIQUE,
  title                     text NOT NULL,
  period_start              date NOT NULL,
  period_end                date NOT NULL,
  
  -- Executive Summary Scalar Metrics
  total_qc_checks           integer NOT NULL DEFAULT 0,
  qc_pass_rate_pct          numeric(5, 2) NOT NULL DEFAULT 0.0,
  total_lab_tests           integer NOT NULL DEFAULT 0,
  lab_pass_rate_pct         numeric(5, 2) NOT NULL DEFAULT 0.0,
  total_capa                integer NOT NULL DEFAULT 0,
  closed_capa               integer NOT NULL DEFAULT 0,
  avg_capa_closure_days     numeric(6, 2),
  total_despatches          integer NOT NULL DEFAULT 0,
  on_time_despatch_rate_pct numeric(5, 2),
  
  -- Multi-Dimensional Metrics Breakdown (Defect breakdown, trends, supplier scorecards)
  metrics_payload           jsonb NOT NULL,
  
  notes                     text,
  generated_by              uuid NOT NULL REFERENCES profiles(id),
  created_at                timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_kpi_snapshots_period ON kpi_report_snapshots(period_start, period_end);
CREATE INDEX IF NOT EXISTS idx_kpi_snapshots_created_at ON kpi_report_snapshots(created_at);

-- Cryptographic Audit Trigger
DROP TRIGGER IF EXISTS audit_kpi_report_snapshots ON kpi_report_snapshots;
CREATE TRIGGER audit_kpi_report_snapshots
  AFTER INSERT OR UPDATE OR DELETE ON kpi_report_snapshots
  FOR EACH ROW EXECUTE FUNCTION log_change();

-- ---------------------------------------------------------------------------
-- 2. Row Level Security (RLS) on kpi_report_snapshots
-- ---------------------------------------------------------------------------

ALTER TABLE kpi_report_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE kpi_report_snapshots FORCE ROW LEVEL SECURITY;

GRANT SELECT, INSERT ON kpi_report_snapshots TO authenticated, snm_app;
GRANT USAGE, SELECT ON SEQUENCE kpi_report_seq TO authenticated, snm_app;

DROP POLICY IF EXISTS kpi_report_snapshots_select ON kpi_report_snapshots;
CREATE POLICY kpi_report_snapshots_select ON kpi_report_snapshots
  FOR SELECT TO authenticated
  USING (
    auth_can('qc', 'read')
    OR auth_can('tests', 'read')
    OR auth_can('audit', 'read')
    OR auth_can('systems', 'read')
    OR auth_can('costing', 'read')
    OR auth_can('jobs', 'read')
  );

DROP POLICY IF EXISTS kpi_report_snapshots_insert ON kpi_report_snapshots;
CREATE POLICY kpi_report_snapshots_insert ON kpi_report_snapshots
  FOR INSERT TO authenticated
  WITH CHECK (
    auth_can('qc', 'approve')
    OR auth_can('qc', 'create')
    OR auth_can('systems', 'create')
  );

-- Deliberately NO UPDATE or DELETE policy on kpi_report_snapshots.
-- Snapshots are immutable compliance records once generated.

-- Cross-functional Quality & Supply Chain Scorecard Visibility
DROP POLICY IF EXISTS suppliers_analytics_select ON suppliers;
CREATE POLICY suppliers_analytics_select ON suppliers
  FOR SELECT TO authenticated
  USING (
    auth_can('qc', 'read') 
    OR auth_can('tests', 'read')
  );

DROP POLICY IF EXISTS lab_tests_supply_chain_select ON lab_tests;
CREATE POLICY lab_tests_supply_chain_select ON lab_tests
  FOR SELECT TO authenticated
  USING (
    auth_can('stock', 'read') 
    OR auth_can('purchase', 'read')
  );


-- ---------------------------------------------------------------------------
-- 3. Public Certificate Verification Function (SECURITY DEFINER)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION verify_public_certificate(
  p_cert_no text,
  p_hash_fragment text
)
RETURNS TABLE (
  cert_no             text,
  status              text,
  issued_at           timestamptz,
  product             text,
  specification       text,
  total_qc_checks     integer,
  total_lab_tests     integer,
  full_sha256_hash    text,
  revoked_at          timestamptz,
  revocation_reason   text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_cleaned_cert text;
  v_cleaned_hash text;
BEGIN
  v_cleaned_cert := upper(trim(COALESCE(p_cert_no, '')));
  v_cleaned_hash := lower(trim(COALESCE(p_hash_fragment, '')));

  -- Fail closed: require non-empty cert_no AND exactly 32 hex characters
  IF v_cleaned_cert = '' OR length(v_cleaned_hash) <> 32 OR NOT (v_cleaned_hash ~ '^[0-9a-f]{32}$') THEN
    RETURN;
  END IF;

  RETURN QUERY
  SELECT 
    tc.cert_no,
    tc.status,
    tc.issued_at,
    j.product,
    COALESCE(j.spec, 'Not specified') AS specification,
    tc.total_qc_checks,
    tc.total_lab_tests,
    tc.sha256_hash AS full_sha256_hash,
    tc.revoked_at,
    tc.revocation_reason
  FROM test_certificates tc
  JOIN jobs j ON j.id = tc.job_id
  WHERE upper(tc.cert_no) = v_cleaned_cert
    AND lower(left(tc.sha256_hash, 32)) = v_cleaned_hash;
END;
$$;

GRANT EXECUTE ON FUNCTION verify_public_certificate(text, text) TO anon, authenticated, snm_app;
