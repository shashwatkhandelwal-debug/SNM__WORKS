-- ============================================================================
-- 27_seed_owner_roles.sql - Bootstrap User Roles for Owner Profile(s)
-- ============================================================================
-- In migration 07_roles.sql, user_roles was populated from profiles WHERE role = 'owner'.
-- Because 07 was applied before user profiles were signed up, and 08_lockdown.sql
-- forced RLS on user_roles, user_roles has 0 rows in production.
-- Running this script in Supabase SQL Editor assigns all roles to owner profiles.
-- ============================================================================

INSERT INTO user_roles (user_id, role_code, active)
SELECT p.id, r.code, true
FROM profiles p
CROSS JOIN roles r
WHERE p.role = 'owner'
ON CONFLICT (user_id, role_code) DO UPDATE SET active = true;

-- Verification
SELECT 
  p.id AS user_id,
  p.full_name,
  p.role AS profile_role,
  COUNT(ur.role_code) AS roles_assigned
FROM profiles p
LEFT JOIN user_roles ur ON ur.user_id = p.id
WHERE p.role = 'owner'
GROUP BY p.id, p.full_name, p.role;
