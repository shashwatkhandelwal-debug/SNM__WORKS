-- ============================================================================
-- SNM WORKS — SHA-256 HASH-CHAINED AUDIT LOG & VERIFICATION
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS prev_hash text;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS curr_hash text;

-- Hash chaining trigger function
CREATE OR REPLACE FUNCTION chain_audit_hash()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public AS $$
DECLARE
  v_prev text;
  v_data text;
BEGIN
  -- Fetch the hash of the latest audit record
  SELECT curr_hash INTO v_prev
  FROM audit_log
  ORDER BY id DESC
  LIMIT 1;

  NEW.prev_hash := COALESCE(v_prev, '0000000000000000000000000000000000000000000000000000000000000000');

  v_data := NEW.prev_hash
    || '|' || COALESCE(NEW.at::text, '')
    || '|' || COALESCE(NEW.actor_id::text, '')
    || '|' || COALESCE(NEW.action, '')
    || '|' || COALESCE(NEW.entity, '')
    || '|' || COALESCE(NEW.entity_ref, '')
    || '|' || COALESCE(NEW.before::text, '')
    || '|' || COALESCE(NEW.after::text, '');

  NEW.curr_hash := encode(digest(v_data, 'sha256'), 'hex');

  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_chain_audit ON audit_log;
CREATE TRIGGER trg_chain_audit
  BEFORE INSERT ON audit_log
  FOR EACH ROW
  EXECUTE FUNCTION chain_audit_hash();

-- Verification function
CREATE OR REPLACE FUNCTION verify_audit_chain()
RETURNS TABLE (status text, broken_at bigint, checked_count bigint)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public AS $$
DECLARE
  r RECORD;
  v_expected_prev text := '0000000000000000000000000000000000000000000000000000000000000000';
  v_calc text;
  v_data text;
  v_count bigint := 0;
BEGIN
  FOR r IN SELECT * FROM audit_log ORDER BY id ASC LOOP
    v_count := v_count + 1;
    
    IF r.prev_hash IS DISTINCT FROM v_expected_prev THEN
      RETURN QUERY SELECT 'BROKEN'::text, r.id, v_count;
      RETURN;
    END IF;

    v_data := r.prev_hash
      || '|' || COALESCE(r.at::text, '')
      || '|' || COALESCE(r.actor_id::text, '')
      || '|' || COALESCE(r.action, '')
      || '|' || COALESCE(r.entity, '')
      || '|' || COALESCE(r.entity_ref, '')
      || '|' || COALESCE(r.before::text, '')
      || '|' || COALESCE(r.after::text, '');

    v_calc := encode(digest(v_data, 'sha256'), 'hex');

    IF r.curr_hash IS DISTINCT FROM v_calc THEN
      RETURN QUERY SELECT 'BROKEN'::text, r.id, v_count;
      RETURN;
    END IF;

    v_expected_prev := r.curr_hash;
  END LOOP;

  RETURN QUERY SELECT 'OK'::text, NULL::bigint, v_count;
END $$;

GRANT EXECUTE ON FUNCTION verify_audit_chain() TO authenticated;
