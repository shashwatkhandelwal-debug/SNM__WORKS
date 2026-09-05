-- ============================================================================
-- SNM WORKS — SPEC PDF INGESTION & SKU SPECIFICATIONS SCHEMA
-- Migration 28
-- ============================================================================
-- 1. Creates spec_upload_status ENUM and spec_pdf_uploads table.
-- 2. Creates sku_specifications many-to-many relationship table.
-- 3. Implements verify_spec_pdf_integrity() with internal auth_can('audit', 'read') guard.
-- 4. Attaches audit log triggers for cryptographic hash chaining.
-- 5. Enables and forces Row Level Security (RLS) with strict RBAC policies.
-- 6. Seeds role_permissions for specifications:create and audit:read.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Status ENUM & spec_pdf_uploads Table
-- ---------------------------------------------------------------------------

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'spec_upload_status') THEN
    CREATE TYPE spec_upload_status AS ENUM (
      'Uploaded',   -- PDF received and stored, pending parser execution
      'Parsed',     -- PDF parsed into JSON, ready for human review
      'Reviewed',   -- Human reviewed and adjusted fields in UI
      'Loaded',     -- Ingested into specifications & spec_variants as Draft
      'Error'       -- Processing or parsing failure
    );
  END IF;
END $$;

-- Align spec_variants columns (designation, sort_order)
ALTER TABLE spec_variants ADD COLUMN IF NOT EXISTS designation text;
ALTER TABLE spec_variants ADD COLUMN IF NOT EXISTS sort_order int NOT NULL DEFAULT 0;

DO $$
BEGIN
  ALTER TABLE spec_variants ALTER COLUMN variant_code DROP NOT NULL;
EXCEPTION
  WHEN undefined_column THEN NULL;
END $$;

DO $$
BEGIN
  ALTER TABLE spec_variants ALTER COLUMN name DROP NOT NULL;
EXCEPTION
  WHEN undefined_column THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS spec_pdf_uploads (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  sku_id                uuid NOT NULL REFERENCES skus(id) ON DELETE CASCADE,
  spec_id               uuid REFERENCES specifications(id) ON DELETE SET NULL,
  
  -- Storage paths and cryptographic SHA-256 hashes
  storage_path          text NOT NULL,
  pdf_sha256            text NOT NULL,
  parsed_json_path      text,
  parsed_json_sha256    text,
  corrected_json_path   text,
  corrected_json_sha256 text,
  
  -- Metadata & Tracking
  source                text NOT NULL DEFAULT 'web_upload' CHECK (source IN ('web_upload', 'folder_watcher')),
  original_filename     text NOT NULL,
  file_size_bytes       bigint NOT NULL DEFAULT 0,
  status                spec_upload_status NOT NULL DEFAULT 'Uploaded',
  error_message         text,
  
  uploaded_by           uuid NOT NULL REFERENCES profiles(id),
  uploaded_at           timestamptz NOT NULL DEFAULT now(),
  reviewed_by           uuid REFERENCES profiles(id),
  reviewed_at           timestamptz,
  loaded_at             timestamptz
);

CREATE INDEX IF NOT EXISTS idx_spec_pdf_uploads_sku ON spec_pdf_uploads(sku_id);
CREATE INDEX IF NOT EXISTS idx_spec_pdf_uploads_status ON spec_pdf_uploads(status);
CREATE INDEX IF NOT EXISTS idx_spec_pdf_uploads_spec ON spec_pdf_uploads(spec_id);

-- ---------------------------------------------------------------------------
-- 2. sku_specifications Many-to-Many Table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sku_specifications (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  sku_id          uuid NOT NULL REFERENCES skus(id) ON DELETE CASCADE,
  spec_id         uuid NOT NULL REFERENCES specifications(id) ON DELETE CASCADE,
  variant_id      uuid REFERENCES spec_variants(id) ON DELETE SET NULL,
  
  relationship    text NOT NULL DEFAULT 'primary' CHECK (relationship IN ('primary', 'color_standard', 'finish_standard', 'packaging', 'secondary')),
  is_primary      boolean NOT NULL DEFAULT false,
  notes           text,
  
  created_by      uuid REFERENCES profiles(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  
  CONSTRAINT uq_sku_spec_variant UNIQUE (sku_id, spec_id, variant_id, relationship)
);

CREATE INDEX IF NOT EXISTS idx_sku_specs_sku ON sku_specifications(sku_id);
CREATE INDEX IF NOT EXISTS idx_sku_specs_spec ON sku_specifications(spec_id);
CREATE INDEX IF NOT EXISTS idx_sku_specs_variant ON sku_specifications(variant_id);

-- ---------------------------------------------------------------------------
-- 3. Two-Tier Hash & Audit Chain Verification Function
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION verify_spec_pdf_integrity(
  p_upload_id               uuid,
  p_actual_pdf_sha256       text,
  p_actual_parsed_sha256    text,
  p_actual_corrected_sha256 text DEFAULT NULL
)
RETURNS TABLE (
  upload_id            uuid,
  sku_code             text,
  pdf_stored_sha256    text,
  pdf_actual_sha256    text,
  pdf_match            boolean,
  parsed_json_match    boolean,
  corrected_json_match boolean,
  audit_chain_valid    boolean,
  status               text,
  details              jsonb
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public AS $$
DECLARE
  v_rec               record;
  v_sku_code          text;
  v_pdf_match         boolean := false;
  v_parsed_match      boolean := false;
  v_corrected_match   boolean := true;
  v_audit_valid       boolean := false;
  v_chain_rec         record;
  v_overall_status    text := 'CORRUPTED';
  v_details           jsonb;
BEGIN
  -- 1. Internal permission guard: Requires audit:read permission
  IF NOT auth_can('audit', 'read') THEN
    RAISE EXCEPTION 'Not authorized to verify spec audit integrity';
  END IF;

  -- 2. Fetch upload record
  SELECT u.*, s.sku_code INTO v_rec
  FROM spec_pdf_uploads u
  JOIN skus s ON s.id = u.sku_id
  WHERE u.id = p_upload_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Upload ID % not found', p_upload_id;
  END IF;

  v_sku_code := v_rec.sku_code;

  -- 3. Verify PDF Hash Match
  IF v_rec.pdf_sha256 IS NOT DISTINCT FROM p_actual_pdf_sha256 THEN
    v_pdf_match := true;
  END IF;

  -- 4. Verify Raw Parsed JSON Hash Match (if parsed)
  IF v_rec.parsed_json_sha256 IS NOT NULL THEN
    IF v_rec.parsed_json_sha256 IS NOT DISTINCT FROM p_actual_parsed_sha256 THEN
      v_parsed_match := true;
    ELSE
      v_parsed_match := false;
    END IF;
  ELSE
    v_parsed_match := true; -- Not yet parsed, so neutral
  END IF;

  -- 5. Verify Corrected JSON Hash Match (if corrected)
  IF v_rec.corrected_json_sha256 IS NOT NULL THEN
    IF v_rec.corrected_json_sha256 IS NOT DISTINCT FROM p_actual_corrected_sha256 THEN
      v_corrected_match := true;
    ELSE
      v_corrected_match := false;
    END IF;
  END IF;

  -- 6. Check SHA-256 Hash Chain Integrity in audit_log
  SELECT c.status INTO v_chain_rec FROM verify_audit_chain() c LIMIT 1;
  IF v_chain_rec.status = 'OK' THEN
    v_audit_valid := true;
  ELSE
    v_audit_valid := false;
  END IF;

  -- 7. Overall Verdict
  IF v_pdf_match AND v_parsed_match AND v_corrected_match AND v_audit_valid THEN
    v_overall_status := 'VALID';
  ELSE
    v_overall_status := 'CORRUPTED';
  END IF;

  v_details := jsonb_build_object(
    'upload_id', v_rec.id,
    'sku_id', v_rec.sku_id,
    'sku_code', v_sku_code,
    'storage_path', v_rec.storage_path,
    'pdf_sha256_stored', v_rec.pdf_sha256,
    'pdf_sha256_actual', p_actual_pdf_sha256,
    'parsed_sha256_stored', v_rec.parsed_json_sha256,
    'parsed_sha256_actual', p_actual_parsed_sha256,
    'corrected_sha256_stored', v_rec.corrected_json_sha256,
    'corrected_sha256_actual', p_actual_corrected_sha256,
    'audit_chain_status', v_chain_rec.status
  );

  RETURN QUERY SELECT 
    v_rec.id,
    v_sku_code,
    v_rec.pdf_sha256,
    p_actual_pdf_sha256,
    v_pdf_match,
    v_parsed_match,
    v_corrected_match,
    v_audit_valid,
    v_overall_status,
    v_details;
END $$;

GRANT EXECUTE ON FUNCTION verify_spec_pdf_integrity(uuid, text, text, text) TO authenticated, snm_app;

-- ---------------------------------------------------------------------------
-- 4. Audit Log Triggers
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS trg_audit_spec_pdf_uploads ON spec_pdf_uploads;
CREATE TRIGGER trg_audit_spec_pdf_uploads
  AFTER INSERT OR UPDATE OR DELETE ON spec_pdf_uploads
  FOR EACH ROW EXECUTE FUNCTION log_change();

DROP TRIGGER IF EXISTS trg_audit_sku_specifications ON sku_specifications;
CREATE TRIGGER trg_audit_sku_specifications
  AFTER INSERT OR UPDATE OR DELETE ON sku_specifications
  FOR EACH ROW EXECUTE FUNCTION log_change();

-- ---------------------------------------------------------------------------
-- 5. Row Level Security (RLS) Configuration
-- ---------------------------------------------------------------------------

ALTER TABLE spec_pdf_uploads ENABLE ROW LEVEL SECURITY;
ALTER TABLE spec_pdf_uploads FORCE ROW LEVEL SECURITY;

ALTER TABLE sku_specifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE sku_specifications FORCE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE ON spec_pdf_uploads TO authenticated, snm_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON sku_specifications TO authenticated, snm_app;

-- spec_pdf_uploads policies
DROP POLICY IF EXISTS spec_pdf_uploads_read ON spec_pdf_uploads;
DROP POLICY IF EXISTS spec_pdf_uploads_insert ON spec_pdf_uploads;
DROP POLICY IF EXISTS spec_pdf_uploads_update ON spec_pdf_uploads;

CREATE POLICY spec_pdf_uploads_read ON spec_pdf_uploads FOR SELECT
  USING (auth_can('specifications', 'read'));

CREATE POLICY spec_pdf_uploads_insert ON spec_pdf_uploads FOR INSERT
  WITH CHECK (auth_can('specifications', 'create'));

CREATE POLICY spec_pdf_uploads_update ON spec_pdf_uploads FOR UPDATE
  USING (auth_can('specifications', 'create') OR auth_can('specifications', 'update'));

-- sku_specifications policies
DROP POLICY IF EXISTS sku_specifications_read ON sku_specifications;
DROP POLICY IF EXISTS sku_specifications_insert ON sku_specifications;
DROP POLICY IF EXISTS sku_specifications_update ON sku_specifications;
DROP POLICY IF EXISTS sku_specifications_delete ON sku_specifications;

CREATE POLICY sku_specifications_read ON sku_specifications FOR SELECT
  USING (auth_can('skus', 'read') OR auth_can('specifications', 'read'));

CREATE POLICY sku_specifications_insert ON sku_specifications FOR INSERT
  WITH CHECK (auth_can('skus', 'create') OR auth_can('specifications', 'create'));

CREATE POLICY sku_specifications_update ON sku_specifications FOR UPDATE
  USING (auth_can('skus', 'update') OR auth_can('specifications', 'update'));

CREATE POLICY sku_specifications_delete ON sku_specifications FOR DELETE
  USING (auth_can('skus', 'update') OR auth_can('specifications', 'update'));

-- Allow users with specifications permissions to read and update skus (e.g. syncing standard field)
DROP POLICY IF EXISTS skus_read ON skus;
CREATE POLICY skus_read ON skus FOR SELECT
  USING (auth_can('skus', 'read') OR auth_can('specifications', 'read') OR auth_can('specifications', 'create'));

DROP POLICY IF EXISTS skus_update ON skus;
CREATE POLICY skus_update ON skus FOR UPDATE
  USING (auth_can('skus', 'update') OR auth_can('specifications', 'create') OR auth_can('specifications', 'update'));

-- ---------------------------------------------------------------------------
-- 6. Role Permissions Seed
-- ---------------------------------------------------------------------------

-- 1. Grant specifications:create to technical, engineering, and quality roles
INSERT INTO role_permissions (role_code, module, action)
VALUES 
  ('product_developer', 'specifications', 'create'),
  ('process_engineer', 'specifications', 'create'),
  ('qa_manager', 'specifications', 'create'),
  ('chief_technical', 'specifications', 'create'),
  ('chief_quality', 'specifications', 'create')
ON CONFLICT (role_code, module, action) DO NOTHING;

-- 2. Grant skus:read to technical and quality roles
INSERT INTO role_permissions (role_code, module, action)
VALUES
  ('product_developer', 'skus', 'read'),
  ('process_engineer', 'skus', 'read'),
  ('qa_manager', 'skus', 'read'),
  ('chief_technical', 'skus', 'read'),
  ('chief_quality', 'skus', 'read')
ON CONFLICT (role_code, module, action) DO NOTHING;

-- 3. Grant audit:read to compliance, quality, and information chiefs
INSERT INTO role_permissions (role_code, module, action)
VALUES
  ('chief_compliance', 'audit', 'read'),
  ('chief_quality', 'audit', 'read'),
  ('chief_information', 'audit', 'read')
ON CONFLICT (role_code, module, action) DO NOTHING;
