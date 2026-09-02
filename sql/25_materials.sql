-- ============================================================================
-- SNM WORKS - MATERIALS, GRN, YARN LOTS, SUPPLIERS & TRACEABILITY
-- Migration 25
-- ============================================================================

-- 1. Suppliers Master Table
CREATE TABLE IF NOT EXISTS suppliers (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  supplier_code  text NOT NULL UNIQUE,                     -- SUP-001
  name           text NOT NULL,                            -- Reliance Industries, SRF, etc.
  contact_person text,
  email          text,
  phone          text,
  address        text,
  active         boolean NOT NULL DEFAULT true,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

-- 2. Sequences for race-free collision-proof numbering
CREATE SEQUENCE IF NOT EXISTS grn_seq START WITH 1;
CREATE SEQUENCE IF NOT EXISTS yarn_lot_seq START WITH 1;
CREATE SEQUENCE IF NOT EXISTS material_issue_seq START WITH 1;

-- 3. Goods Receipt Notes (GRN Header)
CREATE TABLE IF NOT EXISTS grn (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  grn_no          text NOT NULL UNIQUE,                    -- GRN-2026-0001
  received_date   date NOT NULL DEFAULT CURRENT_DATE,
  po_ref          text,
  supplier_id     uuid REFERENCES suppliers(id),
  supplier_name   text NOT NULL,
  carrier_vehicle text,
  invoice_no      text,
  invoice_date    date,
  remarks         text,
  received_by     uuid REFERENCES profiles(id),
  created_by      uuid REFERENCES profiles(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

-- 4. Yarn Lots (Material Lots Received via GRN - 1:Many)
CREATE TABLE IF NOT EXISTS yarn_lots (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lot_no           text NOT NULL UNIQUE,                   -- LOT-2026-0001 (SNM Internal sequence)
  supplier_lot_no  text,                                   -- Manufacturer's Batch / Merge ID
  grn_id           uuid REFERENCES grn(id) ON DELETE SET NULL,
  supplier_id      uuid REFERENCES suppliers(id),
  supplier_name    text NOT NULL,
  yarn_type        text NOT NULL,                          -- Nylon 6,6, Polyester, Cotton, etc.
  denier           numeric NOT NULL CHECK (denier > 0),    -- e.g. 840
  filament_count   int,                                    -- e.g. 140 (840/140)
  lustre           text,                                   -- Bright, Semi-Dull
  colour           text NOT NULL DEFAULT 'Raw White / Ecru',
  qty_received     numeric NOT NULL CHECK (qty_received > 0),
  qty_issued       numeric NOT NULL DEFAULT 0 CHECK (qty_issued >= 0),
  qty_remaining    numeric GENERATED ALWAYS AS (qty_received - qty_issued) STORED,
  unit             text NOT NULL DEFAULT 'kg',
  qc_status        text NOT NULL DEFAULT 'Quarantine' 
                   CHECK (qc_status IN ('Quarantine', 'Approved', 'Rejected')),
  storage_location text,                                   -- e.g. Rack A-12
  received_date    date NOT NULL DEFAULT CURRENT_DATE,
  tested_by        uuid REFERENCES profiles(id),           -- Analyst who ran incoming test
  released_by      uuid REFERENCES profiles(id),           -- QA who released lot
  released_at      timestamptz,
  created_by       uuid REFERENCES profiles(id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT yarn_lots_balance_check CHECK (qty_issued <= qty_received),
  CONSTRAINT yarn_lots_no_self_release CHECK (released_by IS NULL OR tested_by IS NULL OR released_by <> tested_by)
);

-- 5. Extend lab_tests for incoming yarn testing
ALTER TABLE lab_tests 
  ADD COLUMN IF NOT EXISTS yarn_lot_id uuid REFERENCES yarn_lots(id) ON DELETE CASCADE;

-- 6. Job Material Issues (Yarn Lot -> Job Traceability link)
CREATE TABLE IF NOT EXISTS job_material_issues (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  issue_no     text NOT NULL UNIQUE,                       -- ISS-2026-0001
  job_id       uuid NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT,
  yarn_lot_id  uuid NOT NULL REFERENCES yarn_lots(id) ON DELETE RESTRICT,
  qty_issued   numeric NOT NULL CHECK (qty_issued > 0),
  unit         text NOT NULL DEFAULT 'kg',
  issued_date  date NOT NULL DEFAULT CURRENT_DATE,
  issued_by    uuid REFERENCES profiles(id),
  remarks      text,
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- 7. Trigger to maintain yarn lot balance and enforce Approved QC status
CREATE OR REPLACE FUNCTION process_job_material_issue()
RETURNS TRIGGER AS $$
DECLARE
  v_qc_status text;
  v_current_issued numeric;
  v_received numeric;
BEGIN
  -- Lock the yarn_lot row for update to serialize concurrent issue transactions
  SELECT qc_status, qty_issued, qty_received 
  INTO v_qc_status, v_current_issued, v_received
  FROM yarn_lots 
  WHERE id = NEW.yarn_lot_id 
  FOR UPDATE;

  -- Verify yarn lot is QC Approved
  IF v_qc_status <> 'Approved' THEN
    RAISE EXCEPTION 'Cannot issue material from yarn lot % with QC status "%". Only "Approved" lots can be issued to jobs.', 
      NEW.yarn_lot_id, v_qc_status;
  END IF;

  -- Check available balance before applying update
  IF (v_current_issued + NEW.qty_issued) > v_received THEN
    RAISE EXCEPTION 'Insufficient balance in yarn lot %. Remaining: % %, Attempted to issue: % %.',
      NEW.yarn_lot_id, (v_received - v_current_issued), NEW.unit, NEW.qty_issued, NEW.unit;
  END IF;

  -- Update yarn lot qty_issued
  UPDATE yarn_lots
  SET qty_issued = qty_issued + NEW.qty_issued,
      updated_at = now()
  WHERE id = NEW.yarn_lot_id;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_job_material_issue_insert ON job_material_issues;
CREATE TRIGGER trg_job_material_issue_insert
BEFORE INSERT ON job_material_issues
FOR EACH ROW EXECUTE FUNCTION process_job_material_issue();

-- 8. Trigger to enforce incoming lab test (tested_by and PASS verdict) before QA release to 'Approved'
CREATE OR REPLACE FUNCTION validate_yarn_lot_release()
RETURNS TRIGGER AS $$
DECLARE
  v_pass_tests_count int;
BEGIN
  -- If transitioning to Approved
  IF NEW.qc_status = 'Approved' AND (OLD.qc_status IS DISTINCT FROM 'Approved') THEN
    IF NEW.tested_by IS NULL THEN
      RAISE EXCEPTION 'Cannot approve yarn lot %: incoming test analyst (tested_by) is required.', NEW.lot_no;
    END IF;

    IF NEW.released_by IS NULL THEN
      RAISE EXCEPTION 'Cannot approve yarn lot %: QA release authority (released_by) is required.', NEW.lot_no;
    END IF;

    IF NEW.released_by = NEW.tested_by THEN
      RAISE EXCEPTION 'Cannot approve yarn lot %: QA release authority cannot be the test analyst (4-eyes segregation).', NEW.lot_no;
    END IF;

    IF NEW.created_by IS NOT NULL AND NEW.released_by = NEW.created_by THEN
      RAISE EXCEPTION 'Cannot approve yarn lot %: QA release authority cannot be the lot creator (4-eyes segregation).', NEW.lot_no;
    END IF;

    SELECT COUNT(*) INTO v_pass_tests_count
    FROM lab_tests
    WHERE yarn_lot_id = NEW.id AND verdict = 'PASS';

    IF v_pass_tests_count = 0 THEN
      RAISE EXCEPTION 'Cannot approve yarn lot %: no passed incoming lab test report found. At least one PASS lab test is required for QA release.', NEW.lot_no;
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_yarn_lots_validate_release ON yarn_lots;
CREATE TRIGGER trg_yarn_lots_validate_release
BEFORE UPDATE OF qc_status ON yarn_lots
FOR EACH ROW EXECUTE FUNCTION validate_yarn_lot_release();

-- 9. Row Level Security Policies
ALTER TABLE suppliers ENABLE ROW LEVEL SECURITY;
ALTER TABLE grn ENABLE ROW LEVEL SECURITY;
ALTER TABLE yarn_lots ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_material_issues ENABLE ROW LEVEL SECURITY;

-- suppliers
DROP POLICY IF EXISTS suppliers_select ON suppliers;
DROP POLICY IF EXISTS suppliers_insert ON suppliers;
DROP POLICY IF EXISTS suppliers_update ON suppliers;

CREATE POLICY suppliers_select ON suppliers FOR SELECT TO authenticated
  USING (auth_can('purchase', 'read') OR auth_can('stock', 'read'));

CREATE POLICY suppliers_insert ON suppliers FOR INSERT TO authenticated
  WITH CHECK (auth_can('purchase', 'create') OR auth_can('stock', 'create'));

CREATE POLICY suppliers_update ON suppliers FOR UPDATE TO authenticated
  USING (auth_can('purchase', 'update') OR auth_can('stock', 'update'));

-- grn
DROP POLICY IF EXISTS grn_select ON grn;
DROP POLICY IF EXISTS grn_insert ON grn;
DROP POLICY IF EXISTS grn_update ON grn;

CREATE POLICY grn_select ON grn FOR SELECT TO authenticated
  USING (auth_can('purchase', 'read') OR auth_can('stock', 'read'));

CREATE POLICY grn_insert ON grn FOR INSERT TO authenticated
  WITH CHECK (auth_can('purchase', 'create') OR auth_can('stock', 'create'));

CREATE POLICY grn_update ON grn FOR UPDATE TO authenticated
  USING (auth_can('purchase', 'update') OR auth_can('stock', 'update'));

-- yarn_lots
DROP POLICY IF EXISTS yarn_lots_select ON yarn_lots;
DROP POLICY IF EXISTS yarn_lots_insert ON yarn_lots;
DROP POLICY IF EXISTS yarn_lots_update ON yarn_lots;

CREATE POLICY yarn_lots_select ON yarn_lots FOR SELECT TO authenticated
  USING (auth_can('stock', 'read') OR auth_can('tests', 'read') OR auth_can('jobs', 'read'));

CREATE POLICY yarn_lots_insert ON yarn_lots FOR INSERT TO authenticated
  WITH CHECK (auth_can('stock', 'create') OR auth_can('purchase', 'create'));

CREATE POLICY yarn_lots_update ON yarn_lots FOR UPDATE TO authenticated
  USING (auth_can('stock', 'update') OR auth_can('tests', 'approve') OR auth_can('tests', 'create'));

-- job_material_issues
DROP POLICY IF EXISTS issues_select ON job_material_issues;
DROP POLICY IF EXISTS issues_insert ON job_material_issues;

CREATE POLICY issues_select ON job_material_issues FOR SELECT TO authenticated
  USING (auth_can('stock', 'read') OR auth_can('jobs', 'read'));

CREATE POLICY issues_insert ON job_material_issues FOR INSERT TO authenticated
  WITH CHECK (auth_can('stock', 'create') OR auth_can('stock', 'update') OR auth_can('jobs', 'update'));
