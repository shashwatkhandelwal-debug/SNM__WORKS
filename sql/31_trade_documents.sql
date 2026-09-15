-- ============================================================================
-- SNM WORKS — TRADE DOCUMENTS & TALLY PIPELINE SCHEMA
-- Migration 31
-- ============================================================================
-- 1. Types: trade_doc_type, trade_doc_source, trade_doc_status.
-- 2. Tables: trade_documents, trade_document_items, party_ledger_mappings.
-- 3. Row Level Security (RLS) forced with granular module policies.
-- 4. Audit triggers attached to all tables.
-- 5. Role permissions for trade_docs module.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Custom Enum Types
-- ---------------------------------------------------------------------------

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'trade_doc_type') THEN
    CREATE TYPE trade_doc_type AS ENUM ('purchase_bill', 'sales_invoice', 'customer_order');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'trade_doc_source') THEN
    CREATE TYPE trade_doc_source AS ENUM ('email', 'manual');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'trade_doc_status') THEN
    CREATE TYPE trade_doc_status AS ENUM ('Draft', 'Parsed', 'Confirmed', 'SyncedToTally', 'Rejected');
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 2. Party Ledger Mappings (OCR Party Name -> Tally Master Ledger)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS party_ledger_mappings (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  extracted_name    text NOT NULL UNIQUE,
  tally_ledger_name text NOT NULL,
  gstin             text,
  gst_state         text,
  party_type        text CHECK (party_type IN ('supplier', 'customer')),
  confirmed_by      uuid REFERENCES profiles(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_party_ledger_extracted ON party_ledger_mappings(extracted_name);
CREATE INDEX IF NOT EXISTS idx_party_ledger_gstin ON party_ledger_mappings(gstin);

-- ---------------------------------------------------------------------------
-- 3. Trade Documents Table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS trade_documents (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  doc_type            trade_doc_type NOT NULL,
  source              trade_doc_source NOT NULL DEFAULT 'manual',
  status              trade_doc_status NOT NULL DEFAULT 'Draft',
  
  -- Header attributes
  party_name          text NOT NULL,
  tally_ledger_name   text,
  gstin               text,
  doc_number          text NOT NULL,
  doc_date            date NOT NULL,
  supply_type         text DEFAULT 'intra_state' CHECK (supply_type IN ('intra_state', 'inter_state')),
  po_reference        text,
  po_date             date,
  
  -- Totals
  total_taxable_value numeric(14, 2) NOT NULL DEFAULT 0.00,
  total_cgst          numeric(14, 2) NOT NULL DEFAULT 0.00,
  total_sgst          numeric(14, 2) NOT NULL DEFAULT 0.00,
  total_igst          numeric(14, 2) NOT NULL DEFAULT 0.00,
  total_tax           numeric(14, 2) NOT NULL DEFAULT 0.00,
  round_off           numeric(14, 2) NOT NULL DEFAULT 0.00,
  net_payable         numeric(14, 2) NOT NULL DEFAULT 0.00,
  
  -- File storage & traceability
  pdf_path            text,
  parsed_json_path    text,
  pdf_sha256          text,
  raw_textract_json   text,
  notes               text,
  
  -- Lifecycle & Audit
  created_by          uuid REFERENCES profiles(id),
  confirmed_by        uuid REFERENCES profiles(id),
  confirmed_at        timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  
  CONSTRAINT uq_trade_doc_party_no UNIQUE (doc_type, party_name, doc_number)
);

CREATE INDEX IF NOT EXISTS idx_trade_docs_type_status ON trade_documents(doc_type, status);
CREATE INDEX IF NOT EXISTS idx_trade_docs_date ON trade_documents(doc_date);
CREATE INDEX IF NOT EXISTS idx_trade_docs_gstin ON trade_documents(gstin);

-- ---------------------------------------------------------------------------
-- 4. Trade Document Line Items Table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS trade_document_items (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  trade_doc_id        uuid NOT NULL REFERENCES trade_documents(id) ON DELETE CASCADE,
  line_no             integer NOT NULL,
  description         text NOT NULL,
  hsn_sac             text,
  tally_stock_item    text,
  
  qty                 numeric(14, 4) NOT NULL DEFAULT 0.0000,
  unit                text NOT NULL DEFAULT 'PCS',
  rate                numeric(14, 2) NOT NULL DEFAULT 0.00,
  taxable_value       numeric(14, 2) NOT NULL DEFAULT 0.00,
  
  cgst_rate           numeric(5, 2) NOT NULL DEFAULT 0.00,
  cgst_amount         numeric(14, 2) NOT NULL DEFAULT 0.00,
  sgst_rate           numeric(5, 2) NOT NULL DEFAULT 0.00,
  sgst_amount         numeric(14, 2) NOT NULL DEFAULT 0.00,
  igst_rate           numeric(5, 2) NOT NULL DEFAULT 0.00,
  igst_amount         numeric(14, 2) NOT NULL DEFAULT 0.00,
  
  line_total          numeric(14, 2) NOT NULL DEFAULT 0.00,
  created_at          timestamptz NOT NULL DEFAULT now(),
  
  CONSTRAINT uq_trade_doc_item_line UNIQUE (trade_doc_id, line_no)
);

CREATE INDEX IF NOT EXISTS idx_trade_doc_items_doc_id ON trade_document_items(trade_doc_id);

-- ---------------------------------------------------------------------------
-- 5. Audit Logging Triggers
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS trg_audit_party_ledger_mappings ON party_ledger_mappings;
CREATE TRIGGER trg_audit_party_ledger_mappings
  AFTER INSERT OR UPDATE OR DELETE ON party_ledger_mappings
  FOR EACH ROW EXECUTE FUNCTION log_change();

DROP TRIGGER IF EXISTS trg_audit_trade_documents ON trade_documents;
CREATE TRIGGER trg_audit_trade_documents
  AFTER INSERT OR UPDATE OR DELETE ON trade_documents
  FOR EACH ROW EXECUTE FUNCTION log_change();

DROP TRIGGER IF EXISTS trg_audit_trade_document_items ON trade_document_items;
CREATE TRIGGER trg_audit_trade_document_items
  AFTER INSERT OR UPDATE OR DELETE ON trade_document_items
  FOR EACH ROW EXECUTE FUNCTION log_change();

-- ---------------------------------------------------------------------------
-- 6. Row Level Security (RLS) Configuration
-- ---------------------------------------------------------------------------

ALTER TABLE party_ledger_mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE party_ledger_mappings FORCE ROW LEVEL SECURITY;

ALTER TABLE trade_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE trade_documents FORCE ROW LEVEL SECURITY;

ALTER TABLE trade_document_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE trade_document_items FORCE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE ON party_ledger_mappings TO authenticated, snm_app;
GRANT SELECT, INSERT, UPDATE ON trade_documents TO authenticated, snm_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON trade_document_items TO authenticated, snm_app;

-- Policies for party_ledger_mappings
DROP POLICY IF EXISTS party_ledger_mappings_read ON party_ledger_mappings;
CREATE POLICY party_ledger_mappings_read ON party_ledger_mappings FOR SELECT
  USING (auth_can('tally', 'read') OR auth_can('commercial', 'read') OR auth_can('purchase', 'read'));

DROP POLICY IF EXISTS party_ledger_mappings_insert ON party_ledger_mappings;
CREATE POLICY party_ledger_mappings_insert ON party_ledger_mappings FOR INSERT
  WITH CHECK (auth_can('tally', 'create') OR auth_can('tally', 'update') OR auth_can('commercial', 'update') OR auth_can('purchase', 'update'));

DROP POLICY IF EXISTS party_ledger_mappings_update ON party_ledger_mappings;
CREATE POLICY party_ledger_mappings_update ON party_ledger_mappings FOR UPDATE
  USING (auth_can('tally', 'update') OR auth_can('commercial', 'update') OR auth_can('purchase', 'update'))
  WITH CHECK (auth_can('tally', 'update') OR auth_can('commercial', 'update') OR auth_can('purchase', 'update'));

-- Policies for trade_documents
DROP POLICY IF EXISTS trade_documents_read ON trade_documents;
CREATE POLICY trade_documents_read ON trade_documents FOR SELECT
  USING (
    auth_can('tally', 'read') OR 
    auth_can('commercial', 'read') OR 
    auth_can('purchase', 'read') OR 
    auth_can('stock', 'read')
  );

DROP POLICY IF EXISTS trade_documents_insert ON trade_documents;
CREATE POLICY trade_documents_insert ON trade_documents FOR INSERT
  WITH CHECK (
    auth_can('tally', 'create') OR 
    auth_can('commercial', 'create') OR 
    auth_can('purchase', 'create') OR
    auth_can('stock', 'create')
  );

DROP POLICY IF EXISTS trade_documents_update ON trade_documents;
CREATE POLICY trade_documents_update ON trade_documents FOR UPDATE
  USING (
    auth_can('tally', 'update') OR 
    auth_can('commercial', 'update') OR 
    auth_can('purchase', 'update') OR
    auth_can('tally', 'approve')
  )
  WITH CHECK (
    auth_can('tally', 'update') OR 
    auth_can('commercial', 'update') OR 
    auth_can('purchase', 'update') OR
    auth_can('tally', 'approve')
  );

-- Policies for trade_document_items
DROP POLICY IF EXISTS trade_document_items_read ON trade_document_items;
CREATE POLICY trade_document_items_read ON trade_document_items FOR SELECT
  USING (
    auth_can('tally', 'read') OR 
    auth_can('commercial', 'read') OR 
    auth_can('purchase', 'read') OR 
    auth_can('stock', 'read')
  );

DROP POLICY IF EXISTS trade_document_items_insert ON trade_document_items;
CREATE POLICY trade_document_items_insert ON trade_document_items FOR INSERT
  WITH CHECK (
    auth_can('tally', 'create') OR 
    auth_can('tally', 'update') OR 
    auth_can('commercial', 'update') OR 
    auth_can('purchase', 'update')
  );

DROP POLICY IF EXISTS trade_document_items_update ON trade_document_items;
CREATE POLICY trade_document_items_update ON trade_document_items FOR UPDATE
  USING (
    auth_can('tally', 'create') OR 
    auth_can('tally', 'update') OR 
    auth_can('commercial', 'update') OR 
    auth_can('purchase', 'update')
  )
  WITH CHECK (
    auth_can('tally', 'create') OR 
    auth_can('tally', 'update') OR 
    auth_can('commercial', 'update') OR 
    auth_can('purchase', 'update')
  );

DROP POLICY IF EXISTS trade_document_items_delete ON trade_document_items;
CREATE POLICY trade_document_items_delete ON trade_document_items FOR DELETE
  USING (
    auth_can('tally', 'update') OR 
    auth_can('commercial', 'update') OR 
    auth_can('purchase', 'update')
  );

-- ---------------------------------------------------------------------------
-- 7. Role Permissions Seed
-- ---------------------------------------------------------------------------

INSERT INTO role_permissions (role_code, module, action)
VALUES 
  ('chief_financial', 'trade_docs', 'read'),
  ('chief_financial', 'trade_docs', 'create'),
  ('chief_financial', 'trade_docs', 'update'),
  ('chief_financial', 'trade_docs', 'approve'),
  ('accounts_officer', 'trade_docs', 'read'),
  ('accounts_officer', 'trade_docs', 'create'),
  ('accounts_officer', 'trade_docs', 'update'),
  ('costing_analyst', 'trade_docs', 'read'),
  ('chief_commercial', 'trade_docs', 'read'),
  ('chief_commercial', 'trade_docs', 'create'),
  ('chief_commercial', 'trade_docs', 'update'),
  ('sales_executive', 'trade_docs', 'read'),
  ('chief_supply_chain', 'trade_docs', 'read'),
  ('purchase_officer', 'trade_docs', 'read'),
  ('purchase_officer', 'trade_docs', 'create'),
  ('purchase_officer', 'trade_docs', 'update'),
  ('store_keeper', 'trade_docs', 'read'),
  ('chief_executive', 'trade_docs', 'read'),
  ('chief_executive', 'trade_docs', 'create'),
  ('chief_executive', 'trade_docs', 'update'),
  ('chief_executive', 'trade_docs', 'approve'),
  ('chief_information', 'trade_docs', 'read'),
  ('system_admin', 'trade_docs', 'read')
ON CONFLICT (role_code, module, action) DO NOTHING;
