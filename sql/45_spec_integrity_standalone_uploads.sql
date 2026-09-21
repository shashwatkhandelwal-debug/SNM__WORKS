-- 45_spec_integrity_standalone_uploads.sql
-- Allow standalone specification uploads (sku_id IS NULL) to be verified by verify_spec_pdf_integrity()
-- Changes INNER JOIN skus to LEFT JOIN skus in verify_spec_pdf_integrity.

CREATE OR REPLACE FUNCTION public.verify_spec_pdf_integrity(p_upload_id uuid, p_actual_pdf_sha256 text, p_actual_parsed_sha256 text, p_actual_corrected_sha256 text DEFAULT NULL::text)
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
  LEFT JOIN skus s ON s.id = u.sku_id
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
END $function$;
