-- ============================================================================
-- 18_organisation.sql — Role Conflicts Trigger, Self-Grant Prevention & Helpers
-- ============================================================================
-- 1. Enforce No Self-Grant Constraint (Admins cannot grant roles to themselves)
-- 2. Database Trigger trg_check_user_role_conflicts on user_roles
--    - Rejects assignments that create a 'block' severity conflict.
-- ============================================================================

-- 1. Non-Negotiable Rule: No Self-Grant on user_roles
ALTER TABLE user_roles
  DROP CONSTRAINT IF EXISTS user_roles_no_self_grant;

ALTER TABLE user_roles
  ADD CONSTRAINT user_roles_no_self_grant
  CHECK (granted_by IS NULL OR granted_by <> user_id);

-- 2. Trigger Function: check_user_role_conflicts()
CREATE OR REPLACE FUNCTION check_user_role_conflicts()
RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_conflict RECORD;
BEGIN
  -- Only validate active role assignments
  IF NEW.active = true THEN
    -- Check if assigning NEW.role_code conflicts with any other active role held by NEW.user_id
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
$$;

DROP TRIGGER IF EXISTS trg_check_user_role_conflicts ON user_roles;
CREATE TRIGGER trg_check_user_role_conflicts
  BEFORE INSERT OR UPDATE ON user_roles
  FOR EACH ROW
  EXECUTE FUNCTION check_user_role_conflicts();

-- 3. Helper Function: get_user_role_conflicts(p_user_id uuid)
CREATE OR REPLACE FUNCTION get_user_role_conflicts(p_user_id uuid)
RETURNS TABLE (
  role_a text,
  role_b text,
  role_a_name text,
  role_b_name text,
  reason text,
  severity text
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT 
    rc.role_a,
    rc.role_b,
    r1.name AS role_a_name,
    r2.name AS role_b_name,
    rc.reason,
    rc.severity
  FROM role_conflicts rc
  JOIN user_roles ur1 ON ur1.role_code = rc.role_a AND ur1.user_id = p_user_id AND ur1.active = true
  JOIN user_roles ur2 ON ur2.role_code = rc.role_b AND ur2.user_id = p_user_id AND ur2.active = true
  JOIN roles r1 ON r1.code = rc.role_a
  JOIN roles r2 ON r2.code = rc.role_b
  WHERE ur1.role_code < ur2.role_code OR rc.role_a = ur1.role_code;
$$;

GRANT EXECUTE ON FUNCTION get_user_role_conflicts(uuid) TO authenticated;
