-- 37_tasks_read_permission.sql
--
-- tasks_read was  USING (auth.uid() IS NOT NULL)  TO public: ANY signed-in account could read EVERY task
-- (titles, details, who raised what), including accounts with no role. Production 2026-09-21: 5 auth users,
-- 2 with no role assigned, 1 unconfirmed email.
--
-- The RBAC already defines the intent: tasks.read is held by all 29 roles. So this changes nothing for anyone
-- who has a role, and closes the roleless / not-yet-provisioned account case.
-- my_tasks() is SECURITY DEFINER and unaffected.
--
-- Idempotent. ROLLBACK (manual):
--   DROP POLICY IF EXISTS tasks_read ON public.tasks;
--   CREATE POLICY tasks_read ON public.tasks FOR SELECT TO public USING (auth.uid() IS NOT NULL);

DROP POLICY IF EXISTS tasks_read ON public.tasks;
CREATE POLICY tasks_read ON public.tasks
    FOR SELECT TO authenticated
    USING (public.auth_can('tasks', 'read'));
