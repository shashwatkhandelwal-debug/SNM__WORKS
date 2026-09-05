-- ============================================================================
-- SNM WORKS - FULL SUPPLY CHAIN & MATERIAL TRACEABILITY
-- Migration 26
-- ============================================================================
-- 1. Creates job_traceability(p_job_id uuid) function.
-- 2. Enforces database-level auth_can('jobs', 'read') permission guard inside
--    the SECURITY DEFINER function before executing joins.
-- 3. Employs pure LEFT JOINs across job_material_issues, yarn_lots, suppliers,
--    grn, incoming lab_tests, despatch, and test_certificates so that unlinked
--    or partial pipeline states return explicit NULL columns rather than
--    silently dropping rows.
-- 4. Grants EXECUTE to authenticated and snm_app.
-- ============================================================================

CREATE OR REPLACE FUNCTION job_traceability(p_job_id uuid)
RETURNS TABLE (
  job_id                uuid,
  job_no                text,
  job_product           text,
  job_spec              text,
  job_qty_ordered       numeric,
  job_unit              text,
  job_status            text,
  -- Material Issue / Yarn Lot
  issue_id              uuid,
  issue_no              text,
  issue_date            date,
  issue_qty             numeric,
  issue_unit            text,
  yarn_lot_id           uuid,
  lot_no                text,
  supplier_lot_no       text,
  yarn_type             text,
  denier                numeric,
  filament_count        int,
  lustre                text,
  colour                text,
  lot_qc_status         text,
  -- Supplier
  supplier_id           uuid,
  supplier_code         text,
  supplier_name         text,
  -- GRN
  grn_id                uuid,
  grn_no                text,
  grn_date              date,
  po_ref                text,
  invoice_no            text,
  -- Incoming Lab Test (PASS)
  incoming_test_id      uuid,
  incoming_test_no      text,
  incoming_test_date    date,
  incoming_test_verdict text,
  -- Despatch
  despatch_id           uuid,
  despatch_no           text,
  despatch_date         date,
  despatch_qty          numeric,
  despatch_unit         text,
  despatch_invoice_no   text,
  -- Test Certificate
  certificate_id        uuid,
  certificate_no        text,
  certificate_issued_at timestamptz,
  certificate_status    text,
  certificate_hash      text
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  -- Internal permission guard: Verify caller has read permission on jobs module
  IF NOT auth_can('jobs', 'read') THEN
    RAISE EXCEPTION 'Not authorized to view job traceability';
  END IF;

  RETURN QUERY
  SELECT 
    j.id AS job_id,
    j.job_no,
    j.product AS job_product,
    j.spec AS job_spec,
    j.qty_ordered AS job_qty_ordered,
    j.unit AS job_unit,
    j.status::text AS job_status,
    -- Issue / Lot
    jmi.id AS issue_id,
    jmi.issue_no,
    jmi.issued_date AS issue_date,
    jmi.qty_issued AS issue_qty,
    jmi.unit AS issue_unit,
    yl.id AS yarn_lot_id,
    yl.lot_no,
    yl.supplier_lot_no,
    yl.yarn_type,
    yl.denier,
    yl.filament_count,
    yl.lustre,
    yl.colour,
    yl.qc_status AS lot_qc_status,
    -- Supplier
    s.id AS supplier_id,
    s.supplier_code,
    COALESCE(s.name, yl.supplier_name, g.supplier_name) AS supplier_name,
    -- GRN
    g.id AS grn_id,
    g.grn_no,
    g.received_date AS grn_date,
    g.po_ref,
    g.invoice_no,
    -- Incoming Lab Test (PASS)
    lt.id AS incoming_test_id,
    lt.test_id AS incoming_test_no,
    lt.tested_on AS incoming_test_date,
    lt.verdict AS incoming_test_verdict,
    -- Despatch
    d.id AS despatch_id,
    d.despatch_no,
    d.despatched_on AS despatch_date,
    d.qty AS despatch_qty,
    d.unit AS despatch_unit,
    d.invoice_no AS despatch_invoice_no,
    -- Certificate
    tc.id AS certificate_id,
    tc.cert_no AS certificate_no,
    tc.issued_at AS certificate_issued_at,
    tc.status AS certificate_status,
    tc.sha256_hash AS certificate_hash
  FROM jobs j
  LEFT JOIN job_material_issues jmi ON jmi.job_id = j.id
  LEFT JOIN yarn_lots yl ON yl.id = jmi.yarn_lot_id
  LEFT JOIN suppliers s ON s.id = yl.supplier_id
  LEFT JOIN grn g ON g.id = yl.grn_id
  LEFT JOIN LATERAL (
    SELECT lt_sub.id, lt_sub.test_id, lt_sub.tested_on, lt_sub.verdict
    FROM lab_tests lt_sub
    WHERE lt_sub.yarn_lot_id = yl.id AND lt_sub.verdict = 'PASS'
    ORDER BY lt_sub.created_at DESC
    LIMIT 1
  ) lt ON true
  LEFT JOIN despatch d ON d.job_id = j.id
  LEFT JOIN LATERAL (
    SELECT tc_sub.id, tc_sub.cert_no, tc_sub.issued_at, tc_sub.status, tc_sub.sha256_hash
    FROM test_certificates tc_sub
    WHERE (d.id IS NOT NULL AND tc_sub.despatch_id = d.id)
       OR (d.id IS NULL AND tc_sub.job_id = j.id)
    ORDER BY (tc_sub.status = 'Issued') DESC, tc_sub.issued_at DESC NULLS LAST
    LIMIT 1
  ) tc ON true
  WHERE j.id = p_job_id
  ORDER BY jmi.created_at ASC NULLS LAST, d.created_at ASC NULLS LAST;
END;
$$;

-- Permissions
REVOKE ALL ON FUNCTION job_traceability(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION job_traceability(uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION job_traceability(uuid) TO snm_app;
