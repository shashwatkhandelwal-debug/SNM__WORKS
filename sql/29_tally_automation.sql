-- ============================================================================
-- SNM WORKS — TALLY AUTOMATION PIPELINE SCHEMA
-- Migration 29
-- ============================================================================
-- 1. Extends customers, skus, suppliers with Tally master data and GST attributes.
-- 2. Extends jobs with commercial order fields (po_reference, po_date, agreed_rate, etc.).
-- 3. Creates tally_voucher_type, tally_source_type, tally_sync_status ENUMs and tally_sync_log table.
-- 4. Enables and forces Row Level Security (RLS) on tally_sync_log and adds additive policies.
-- 5. Seeds role_permissions for tally module (read, create, update, approve).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Master Data & Jobs Schema Extensions
-- ---------------------------------------------------------------------------

-- 1.1 Extend customers with Tally accounting & GST attributes
ALTER TABLE customers 
  ADD COLUMN IF NOT EXISTS tally_ledger_name text,
  ADD COLUMN IF NOT EXISTS gst_state text;

-- 1.2 Extend skus with Tally inventory attributes
ALTER TABLE skus 
  ADD COLUMN IF NOT EXISTS tally_stock_item_name text,
  ADD COLUMN IF NOT EXISTS hsn_code text,
  ADD COLUMN IF NOT EXISTS tally_unit text DEFAULT 'MTR';

-- 1.3 Extend suppliers with Tally accounting & GST attributes
ALTER TABLE suppliers 
  ADD COLUMN IF NOT EXISTS tally_ledger_name text,
  ADD COLUMN IF NOT EXISTS gst_state text,
  ADD COLUMN IF NOT EXISTS gstin text;

-- 1.4 Extend jobs with commercial order fields
ALTER TABLE jobs 
  ADD COLUMN IF NOT EXISTS po_reference text,
  ADD COLUMN IF NOT EXISTS po_date date,
  ADD COLUMN IF NOT EXISTS agreed_rate numeric CHECK (agreed_rate IS NULL OR agreed_rate >= 0),
  ADD COLUMN IF NOT EXISTS agreed_qty numeric CHECK (agreed_qty IS NULL OR agreed_qty > 0),
  ADD COLUMN IF NOT EXISTS agreed_unit text DEFAULT 'm';

-- ---------------------------------------------------------------------------
-- 2. Tally Sync Log Table
-- ---------------------------------------------------------------------------

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'tally_voucher_type') THEN
    CREATE TYPE tally_voucher_type AS ENUM ('Sales', 'Purchase');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'tally_source_type') THEN
    CREATE TYPE tally_source_type AS ENUM ('despatch', 'grn');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'tally_sync_status') THEN
    CREATE TYPE tally_sync_status AS ENUM ('Stubbed', 'Success', 'Failed');
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS tally_sync_log (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  voucher_type      tally_voucher_type NOT NULL,
  source_type       tally_source_type NOT NULL,
  source_id         uuid NOT NULL,
  
  -- Tracking identifiers & summary
  voucher_number    text NOT NULL,
  party_ledger_name text,
  total_amount      numeric(14, 2),
  
  -- XML payloads and Gateway simulation
  xml_payload       text NOT NULL,
  status            tally_sync_status NOT NULL DEFAULT 'Stubbed',
  response_payload  text,
  error_message     text,
  
  created_by        uuid REFERENCES profiles(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tally_sync_source ON tally_sync_log(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_tally_sync_status ON tally_sync_log(status);
CREATE INDEX IF NOT EXISTS idx_tally_sync_vch_type ON tally_sync_log(voucher_type);

-- Audit log trigger
DROP TRIGGER IF EXISTS trg_audit_tally_sync_log ON tally_sync_log;
CREATE TRIGGER trg_audit_tally_sync_log
  AFTER INSERT OR UPDATE OR DELETE ON tally_sync_log
  FOR EACH ROW EXECUTE FUNCTION log_change();

-- ---------------------------------------------------------------------------
-- 3. Row Level Security (RLS) Configuration
-- ---------------------------------------------------------------------------

ALTER TABLE tally_sync_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE tally_sync_log FORCE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE ON tally_sync_log TO authenticated, snm_app;

DROP POLICY IF EXISTS tally_sync_log_read ON tally_sync_log;
DROP POLICY IF EXISTS tally_sync_log_insert ON tally_sync_log;
DROP POLICY IF EXISTS tally_sync_log_update ON tally_sync_log;

CREATE POLICY tally_sync_log_read ON tally_sync_log FOR SELECT
  USING (auth_can('tally', 'read'));

CREATE POLICY tally_sync_log_insert ON tally_sync_log FOR INSERT
  WITH CHECK (auth_can('tally', 'create') OR auth_can('despatch', 'create') OR auth_can('stock', 'create') OR auth_can('purchase', 'create'));

CREATE POLICY tally_sync_log_update ON tally_sync_log FOR UPDATE
  USING (auth_can('tally', 'update') OR auth_can('tally', 'create'));

-- Additive policies allowing tally roles to read and update master mapping fields
DROP POLICY IF EXISTS customers_tally_read ON customers;
CREATE POLICY customers_tally_read ON customers FOR SELECT
  USING (auth_can('tally', 'read'));

DROP POLICY IF EXISTS customers_tally_update ON customers;
CREATE POLICY customers_tally_update ON customers FOR UPDATE
  USING (auth_can('customers', 'update') OR auth_can('tally', 'update'))
  WITH CHECK (auth_can('customers', 'update') OR auth_can('tally', 'update'));

DROP POLICY IF EXISTS skus_tally_read ON skus;
CREATE POLICY skus_tally_read ON skus FOR SELECT
  USING (auth_can('tally', 'read'));

DROP POLICY IF EXISTS skus_tally_update ON skus;
CREATE POLICY skus_tally_update ON skus FOR UPDATE
  USING (auth_can('skus', 'update') OR auth_can('tally', 'update'))
  WITH CHECK (auth_can('skus', 'update') OR auth_can('tally', 'update'));

DROP POLICY IF EXISTS suppliers_tally_read ON suppliers;
CREATE POLICY suppliers_tally_read ON suppliers FOR SELECT
  USING (auth_can('tally', 'read'));

DROP POLICY IF EXISTS suppliers_tally_update ON suppliers;
CREATE POLICY suppliers_tally_update ON suppliers FOR UPDATE
  USING (auth_can('stock', 'update') OR auth_can('tally', 'update'))
  WITH CHECK (auth_can('stock', 'update') OR auth_can('tally', 'update'));

-- ---------------------------------------------------------------------------
-- 4. Role Permissions Seed
-- ---------------------------------------------------------------------------

INSERT INTO role_permissions (role_code, module, action)
VALUES 
  ('chief_financial', 'tally', 'read'),
  ('chief_financial', 'tally', 'create'),
  ('chief_financial', 'tally', 'update'),
  ('chief_financial', 'tally', 'approve'),
  ('accounts_officer', 'tally', 'read'),
  ('accounts_officer', 'tally', 'create'),
  ('accounts_officer', 'tally', 'update'),
  ('costing_analyst', 'tally', 'read'),
  ('chief_commercial', 'tally', 'read'),
  ('sales_executive', 'tally', 'read'),
  ('chief_supply_chain', 'tally', 'read'),
  ('purchase_officer', 'tally', 'read'),
  ('store_keeper', 'tally', 'read'),
  ('chief_executive', 'tally', 'read'),
  ('chief_executive', 'tally', 'create'),
  ('chief_executive', 'tally', 'update'),
  ('chief_information', 'tally', 'read'),
  ('system_admin', 'tally', 'read')
ON CONFLICT (role_code, module, action) DO NOTHING;
