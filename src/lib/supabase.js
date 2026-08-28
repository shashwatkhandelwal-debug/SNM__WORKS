import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL;
const key = import.meta.env.VITE_SUPABASE_KEY;

if (!url || !key) {
  throw new Error(
    "Missing VITE_SUPABASE_URL or VITE_SUPABASE_KEY. Check .env sits in the " +
    "project root, then stop and restart 'npm run dev'."
  );
}

export const supabase = createClient(url, key, {
  auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false },
});

/**
 * Returns one of:
 *   { state: 'signedout' }
 *   { state: 'ok', profile }
 *   { state: 'noprofile', email, uid }   signed in, but no profiles row
 *   { state: 'inactive', profile }       profile exists but switched off
 *   { state: 'error', message }          the query itself failed
 */
export async function loadSession() {
  const { data: { user }, error: authErr } = await supabase.auth.getUser();
  if (authErr) return { state: "error", message: "Auth: " + authErr.message };
  if (!user) return { state: "signedout" };

  const { data, error } = await supabase
    .from("profiles")
    .select("id, full_name, role, active")
    .eq("id", user.id)
    .maybeSingle();

  if (error) return { state: "error", message: "Profile lookup: " + error.message };
  if (!data) return { state: "noprofile", email: user.email, uid: user.id };
  if (!data.active) return { state: "inactive", profile: { ...data, email: user.email } };

  return { state: "ok", profile: { ...data, email: user.email } };
}
