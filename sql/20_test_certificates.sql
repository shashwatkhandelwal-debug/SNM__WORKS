-- ============================================================================
-- 20_test_certificates.sql — Test Certificate of Conformance Issuance Trail
-- ============================================================================
-- 1. Creates sequence for thread-safe human-facing certificate numbering (TC-YYYY-XXXX).
-- 2. Creates test_certificates table to durably track issued customer certificates,
--    issuer identity, inspection counts, and SHA-256 payload digests.
-- 3. Enforces unique active certificate per despatch note.
-- 4. Registers audit trigger logging every issuance and revocation to audit_log.
-- 5. Enables and forces Row Level Security (FORCE RLS).
-- 6. Creates pure auth_can()-based RLS policies for SELECT, INSERT, UPDATE.
-- 7. Grants permissions to snm_app role.
-- ============================================================================

CREATE SEQUENCE IF NOT EXISTS test_certificate_seq START WITH 1 INCREMENT BY 1;

CREATE TABLE IF NOT EXISTS test_certificates (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cert_no             text NOT NULL UNIQUE,
  job_id              uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  despatch_id         uuid REFERENCES despatch(id) ON DELETE SET NULL,
  issued_by           uuid NOT NULL REFERENCES profiles(id),
  issued_at           timestamptz NOT NULL DEFAULT now(),
  sha256_hash         text NOT NULL,
  total_qc_checks     integer NOT NULL DEFAULT 0,
  total_lab_tests     integer NOT NULL DEFAULT 0,
  status              text NOT NULL DEFAULT 'Issued' CHECK (status IN ('Issued', 'Revoked')),
  revoked_by          uuid REFERENCES profiles(id),
  revoked_at          timestamptz,
  revocation_reason   text
);

-- Indexing for performance and lookup
CREATE INDEX IF NOT EXISTS test_certificates_job_id_idx ON test_certificates(job_id);
CREATE INDEX IF NOT EXISTS test_certificates_issued_by_idx ON test_certificates(issued_by);
CREATE INDEX IF NOT EXISTS test_certificates_status_idx ON test_certificates(status);

-- Scoping: Prevent multiple active issued certificates for the exact same despatch note
CREATE UNIQUE INDEX IF NOT EXISTS test_certificates_active_despatch_idx
  ON test_certificates(despatch_id)
  WHERE despatch_id IS NOT NULL AND status = 'Issued';

-- Audit logging trigger
DROP TRIGGER IF EXISTS audit_test_certificates ON test_certificates;
CREATE TRIGGER audit_test_certificates
  AFTER INSERT OR UPDATE OR DELETE ON test_certificates
  FOR EACH ROW EXECUTE FUNCTION log_change();

-- 5. Row Level Security Lockdown
ALTER TABLE test_certificates ENABLE ROW LEVEL SECURITY;
ALTER TABLE test_certificates FORCE ROW LEVEL SECURITY;

-- 6. auth_can() Based Security Policies
DROP POLICY IF EXISTS test_certificates_select ON test_certificates;
CREATE POLICY test_certificates_select ON test_certificates
  FOR SELECT TO authenticated
  USING (
    auth_can('tests', 'read') 
    OR auth_can('despatch', 'read') 
    OR auth_can('qc', 'read')
    OR auth_can('jobs', 'read')
  );

DROP POLICY IF EXISTS test_certificates_insert ON test_certificates;
CREATE POLICY test_certificates_insert ON test_certificates
  FOR INSERT TO authenticated
  WITH CHECK (
    auth_can('tests', 'release') 
    OR auth_can('tests', 'approve')
  );

DROP POLICY IF EXISTS test_certificates_update ON test_certificates;
CREATE POLICY test_certificates_update ON test_certificates
  FOR UPDATE TO authenticated
  USING (
    auth_can('tests', 'release') 
    OR auth_can('tests', 'approve')
  );

-- 7. Grant access to snm_app role
GRANT SELECT, INSERT, UPDATE ON test_certificates TO snm_app;
GRANT USAGE, SELECT ON SEQUENCE test_certificate_seq TO snm_app;
