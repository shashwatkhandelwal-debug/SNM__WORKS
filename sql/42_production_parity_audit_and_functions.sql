-- 42_production_parity_audit_and_functions.sql
--
-- LOCAL PARITY MIGRATION. Makes the local test schema match what is actually running on the Supabase project
-- (definitions read from the project on 2026-09-21). It must NEVER change the project:
--   * skipped when supabase_migrations.schema_migrations exists (that schema exists only on the project)
--   * skipped when chain_audit_row() already exists (parity already applied / project state)
-- Acceptance is objective: after this file, `python tests/drift_compare.py` must show identical hashes for every function
-- and for audit_log columns + triggers (see tests/production_manifest_2026-09-21.txt).
--
-- What differed (local repo vs project):
--   audit chain : local curr_hash + unlocked chain_audit_hash()/trg_chain_audit;  project: row_hash + chain_audit_row()
--                 (takes pg_advisory_xact_lock, so simultaneous writers cannot fork the chain) + trigger audit_chain;
--                 different verify_audit_chain() result columns; audit_daily_root() exists only on the project
--   functions   : check_user_role_conflicts (project exempts owner-tier profiles), freeze_approved_costing (project allows
--                 non-cost edits and covers Archived), get_user_role_conflicts (different result columns), lab_test_verdict
--                 (different logic and 'Pending' casing), verify_public_certificate / verify_spec_pdf_integrity (comment-only)
-- Any local test that assumed the old local behaviour must be reported, not weakened.

DO $parity$
DECLARE
    r      record;
    v_prev text;
    v_hash text;
BEGIN
    IF to_regclass('supabase_migrations.schema_migrations') IS NOT NULL THEN
        RAISE NOTICE '42-skip: Supabase project detected';
        RETURN;
    END IF;
    IF to_regprocedure('public.chain_audit_row()') IS NOT NULL THEN
        RAISE NOTICE '42-skip: parity already applied (chain_audit_row exists)';
        RETURN;
    END IF;

    -- 1. audit_log: curr_hash -> row_hash
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'audit_log' AND column_name = 'curr_hash')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'audit_log' AND column_name = 'row_hash') THEN
        ALTER TABLE public.audit_log RENAME COLUMN curr_hash TO row_hash;
    END IF;

    -- 2. chain trigger function, trigger, and removal of the old unlocked version
    EXECUTE $def$CREATE OR REPLACE FUNCTION public.chain_audit_row()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'public', 'extensions'
AS $function$
declare
  v_prev text;
  v_payload text;
begin
  -- Serialise insertions so two simultaneous writes cannot both claim the
  -- same predecessor and fork the chain. Released at end of transaction.
  perform pg_advisory_xact_lock(hashtext('snm_audit_chain'));

  select row_hash into v_prev
  from audit_log
  order by id desc
  limit 1;

  new.prev_hash := coalesce(v_prev, repeat('0', 64));   -- genesis

  v_payload :=
      new.prev_hash
    || coalesce(new.id::text, '')
    || coalesce(new.at::text, '')
    || coalesce(new.actor_id::text, '')
    || coalesce(new.actor_name, '')
    || coalesce(new.action, '')
    || coalesce(new.entity, '')
    || coalesce(new.entity_ref, '')
    || coalesce(new.before::text, '')
    || coalesce(new.after::text, '');

  new.row_hash := encode(digest(v_payload, 'sha256'), 'hex');
  return new;
end $function$
$def$;

    DROP TRIGGER IF EXISTS trg_chain_audit ON public.audit_log;
    DROP TRIGGER IF EXISTS audit_chain ON public.audit_log;
    CREATE TRIGGER audit_chain BEFORE INSERT ON public.audit_log FOR EACH ROW EXECUTE FUNCTION chain_audit_row();
    DROP FUNCTION IF EXISTS public.chain_audit_hash();

    -- 3. re-hash any rows already in the local test database with the project's scheme (no rows are deleted)
    v_prev := repeat('0', 64);
    FOR r IN SELECT * FROM public.audit_log ORDER BY id ASC LOOP
        v_hash := encode(digest(
              v_prev
           || coalesce(r.id::text, '')
           || coalesce(r.at::text, '')
           || coalesce(r.actor_id::text, '')
           || coalesce(r.actor_name, '')
           || coalesce(r.action, '')
           || coalesce(r.entity, '')
           || coalesce(r.entity_ref, '')
           || coalesce(r.before::text, '')
           || coalesce(r.after::text, ''), 'sha256'), 'hex');
        UPDATE public.audit_log SET prev_hash = v_prev, row_hash = v_hash WHERE id = r.id;
        v_prev := v_hash;
    END LOOP;

    -- 4. verify_audit_chain with the project's result columns (return type differs, so drop first)
    DROP FUNCTION IF EXISTS public.verify_audit_chain();
    EXECUTE $def$CREATE OR REPLACE FUNCTION public.verify_audit_chain()
 RETURNS TABLE(status text, rows_checked bigint, first_bad_id bigint, detail text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'extensions'
AS $function$
declare
  r          record;
  v_prev     text := repeat('0', 64);
  v_expected text;
  v_count    bigint := 0;
begin
  for r in select * from audit_log order by id asc loop
    v_count := v_count + 1;

    if r.prev_hash is distinct from v_prev then
      return query select
        'BROKEN'::text, v_count, r.id,
        'Row does not follow the previous row. A row was altered, inserted or deleted.'::text;
      return;
    end if;

    v_expected := encode(digest(
        r.prev_hash
     || coalesce(r.id::text, '')
     || coalesce(r.at::text, '')
     || coalesce(r.actor_id::text, '')
     || coalesce(r.actor_name, '')
     || coalesce(r.action, '')
     || coalesce(r.entity, '')
     || coalesce(r.entity_ref, '')
     || coalesce(r.before::text, '')
     || coalesce(r.after::text, ''), 'sha256'), 'hex');

    if r.row_hash is distinct from v_expected then
      return query select
        'BROKEN'::text, v_count, r.id,
        'Row contents were changed after it was written.'::text;
      return;
    end if;

    v_prev := r.row_hash;
  end loop;

  return query select
    'OK'::text, v_count, null::bigint,
    'Every row matches its hash and follows the one before it.'::text;
end $function$
$def$;

    -- 5. audit_daily_root exists only on the project today
    EXECUTE $def$CREATE OR REPLACE FUNCTION public.audit_daily_root(p_day date DEFAULT CURRENT_DATE)
 RETURNS TABLE(day date, events bigint, root_hash text)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
  select
    p_day,
    count(*)::bigint,
    (select row_hash from audit_log
      where at::date = p_day order by id desc limit 1)
  from audit_log
  where at::date = p_day
$function$
$def$;

    -- 6. same-signature replacements (privileges are kept by CREATE OR REPLACE)
    EXECUTE $def$CREATE OR REPLACE FUNCTION public.check_user_role_conflicts()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
DECLARE
  v_conflict RECORD;
  v_profile_role text;
BEGIN
  IF NEW.active = true THEN
    -- Exempt owner-tier profiles from segregation-of-duties enforcement.
    -- Owners aren't hired employees subject to internal collusion controls.
    SELECT role INTO v_profile_role FROM profiles WHERE id = NEW.user_id;
    IF v_profile_role = 'owner' THEN
      RETURN NEW;
    END IF;

    SELECT rc.role_a, rc.role_b, rc.reason, rc.severity
    INTO v_conflict
    FROM role_conflicts rc
    JOIN user_roles ur ON (
      (ur.role_code = rc.role_a AND rc.role_b = NEW.role_code)
      OR
      (ur.role_code = rc.role_b AND rc.role_a = NEW.role_code)
    )
    WHERE ur.user_id = NEW.user_id
      AND ur.active = true
      AND ur.role_code <> NEW.role_code
      AND rc.severity = 'block'
    LIMIT 1;

    IF FOUND THEN
      RAISE EXCEPTION 'Segregation of duties violation: Cannot assign role ''%'' to user ''%'' due to a blocking conflict with existing role (Reason: %).',
        NEW.role_code, NEW.user_id, v_conflict.reason
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  RETURN NEW;
END;
$function$
$def$;

    EXECUTE $def$CREATE OR REPLACE FUNCTION public.freeze_approved_costing()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
BEGIN
  IF OLD.status IN ('Approved', 'Archived') THEN
    IF (NEW.qty IS DISTINCT FROM OLD.qty) OR
       (NEW.unit IS DISTINCT FROM OLD.unit) OR
       (NEW.yarn_rate IS DISTINCT FROM OLD.yarn_rate) OR
       (NEW.yarn_consumption IS DISTINCT FROM OLD.yarn_consumption) OR
       (NEW.wastage_pct IS DISTINCT FROM OLD.wastage_pct) OR
       (NEW.dyeing IS DISTINCT FROM OLD.dyeing) OR
       (NEW.coating IS DISTINCT FROM OLD.coating) OR
       (NEW.labour IS DISTINCT FROM OLD.labour) OR
       (NEW.overhead IS DISTINCT FROM OLD.overhead) OR
       (NEW.packing IS DISTINCT FROM OLD.packing) OR
       (NEW.freight IS DISTINCT FROM OLD.freight) OR
       (NEW.margin_pct IS DISTINCT FROM OLD.margin_pct) THEN
      RAISE EXCEPTION 'Cannot modify cost parameters on an approved cost sheet. Cost figures are frozen once approved.'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END;
$function$
$def$;

    EXECUTE $def$CREATE OR REPLACE FUNCTION public.lab_test_verdict(p_limit_type limit_kind, p_spec_value numeric, p_tolerance numeric, p_upper_limit numeric, p_is_critical boolean, p_specimens numeric[])
 RETURNS text
 LANGUAGE plpgsql
 IMMUTABLE
 SET search_path TO 'pg_catalog', 'public'
AS $function$
DECLARE
  v_len int;
  v_min numeric;
  v_max numeric;
  v_avg numeric;
  v_val numeric;
  v_tol numeric := COALESCE(p_tolerance, 0);
  v_upper numeric := COALESCE(p_upper_limit, p_spec_value);
BEGIN
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

  IF p_is_critical THEN
    IF p_limit_type = 'minimum' THEN
      IF v_min >= p_spec_value THEN RETURN 'PASS'; ELSE RETURN 'FAIL'; END IF;
    ELSIF p_limit_type = 'maximum' THEN
      IF v_max <= p_spec_value THEN RETURN 'PASS'; ELSE RETURN 'FAIL'; END IF;
    ELSIF p_limit_type = 'range' THEN
      IF v_min >= p_spec_value AND v_max <= v_upper THEN RETURN 'PASS'; ELSE RETURN 'FAIL'; END IF;
    ELSIF p_limit_type = 'nominal' THEN
      FOREACH v_val IN ARRAY p_specimens LOOP
        IF abs(v_val - p_spec_value) > v_tol THEN RETURN 'FAIL'; END IF;
      END LOOP;
      RETURN 'PASS';
    END IF;
  ELSE
    IF p_limit_type = 'minimum' THEN
      IF v_avg >= p_spec_value THEN RETURN 'PASS'; ELSE RETURN 'FAIL'; END IF;
    ELSIF p_limit_type = 'maximum' THEN
      IF v_avg <= p_spec_value THEN RETURN 'PASS'; ELSE RETURN 'FAIL'; END IF;
    ELSIF p_limit_type = 'range' THEN
      IF v_avg >= p_spec_value AND v_avg <= v_upper THEN RETURN 'PASS'; ELSE RETURN 'FAIL'; END IF;
    ELSIF p_limit_type = 'nominal' THEN
      IF abs(v_avg - p_spec_value) <= v_tol THEN RETURN 'PASS'; ELSE RETURN 'FAIL'; END IF;
    END IF;
  END IF;

  RETURN 'FAIL';
END $function$
$def$;

    EXECUTE $def$CREATE OR REPLACE FUNCTION public.verify_public_certificate(p_cert_no text, p_hash_fragment text)
 RETURNS TABLE(cert_no text, status text, issued_at timestamp with time zone, product text, specification text, total_qc_checks integer, total_lab_tests integer, full_sha256_hash text, revoked_at timestamp with time zone, revocation_reason text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
  v_cleaned_cert text;
  v_cleaned_hash text;
BEGIN
  v_cleaned_cert := upper(trim(COALESCE(p_cert_no, '')));
  v_cleaned_hash := lower(trim(COALESCE(p_hash_fragment, '')));

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
$function$
$def$;

    EXECUTE $def$CREATE OR REPLACE FUNCTION public.verify_spec_pdf_integrity(p_upload_id uuid, p_actual_pdf_sha256 text, p_actual_parsed_sha256 text, p_actual_corrected_sha256 text DEFAULT NULL::text)
 RETURNS TABLE(upload_id uuid, sku_code text, pdf_stored_sha256 text, pdf_actual_sha256 text, pdf_match boolean, parsed_json_match boolean, corrected_json_match boolean, audit_chain_valid boolean, status text, details jsonb)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
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
  IF NOT auth_can('audit', 'read') THEN
    RAISE EXCEPTION 'Not authorized to verify spec audit integrity';
  END IF;

  SELECT u.*, s.sku_code INTO v_rec
  FROM spec_pdf_uploads u
  JOIN skus s ON s.id = u.sku_id
  WHERE u.id = p_upload_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Upload ID % not found', p_upload_id;
  END IF;

  v_sku_code := v_rec.sku_code;

  IF v_rec.pdf_sha256 IS NOT DISTINCT FROM p_actual_pdf_sha256 THEN
    v_pdf_match := true;
  END IF;

  IF v_rec.parsed_json_sha256 IS NOT NULL THEN
    IF v_rec.parsed_json_sha256 IS NOT DISTINCT FROM p_actual_parsed_sha256 THEN
      v_parsed_match := true;
    ELSE
      v_parsed_match := false;
    END IF;
  ELSE
    v_parsed_match := true;
  END IF;

  IF v_rec.corrected_json_sha256 IS NOT NULL THEN
    IF v_rec.corrected_json_sha256 IS NOT DISTINCT FROM p_actual_corrected_sha256 THEN
      v_corrected_match := true;
    ELSE
      v_corrected_match := false;
    END IF;
  END IF;

  SELECT c.status INTO v_chain_rec FROM verify_audit_chain() c LIMIT 1;
  IF v_chain_rec.status = 'OK' THEN
    v_audit_valid := true;
  ELSE
    v_audit_valid := false;
  END IF;

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
END $function$
$def$;

    -- 7. get_user_role_conflicts: different result columns on the project (drop first), then restore the app's access
    DROP FUNCTION IF EXISTS public.get_user_role_conflicts(uuid);
    EXECUTE $def$CREATE OR REPLACE FUNCTION public.get_user_role_conflicts(p_user_id uuid)
 RETURNS TABLE(role_a text, role_b text, role_a_name text, role_b_name text, reason text, severity text)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
  SELECT rc.role_a, rc.role_b, r1.name, r2.name, rc.reason, rc.severity
  FROM role_conflicts rc
  JOIN user_roles ur1 ON ur1.role_code = rc.role_a AND ur1.user_id = p_user_id AND ur1.active = true
  JOIN user_roles ur2 ON ur2.role_code = rc.role_b AND ur2.user_id = p_user_id AND ur2.active = true
  JOIN roles r1 ON r1.code = rc.role_a
  JOIN roles r2 ON r2.code = rc.role_b
  WHERE ur1.role_code < ur2.role_code OR rc.role_a = ur1.role_code;
$function$
$def$;
    GRANT EXECUTE ON FUNCTION public.get_user_role_conflicts(uuid) TO authenticated;

    RAISE NOTICE '42: parity applied (audit chain + functions)';
END
$parity$;
