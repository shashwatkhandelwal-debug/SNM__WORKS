import { useState, useEffect } from "react";
import { supabase, loadSession } from "./lib/supabase";
import { loadPermissions } from "./lib/moduleKit";
import { modules } from "./modules";
import Register from "./components/Register";
import Specifications from "./screens/Specifications";

export default function App() {
  const [loading, setLoading] = useState(true);
  const [session, setSession] = useState({ state: "signedout" });

  async function refresh() {
    try {
      setSession(await loadSession());
    } catch (e) {
      setSession({ state: "error", message: e?.message || String(e) });
    }
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial session fetch, not a synchronous set
    refresh().finally(() => setLoading(false));
    const { data: sub } = supabase.auth.onAuthStateChange(() => refresh());
    return () => sub.subscription.unsubscribe();
  }, []);

  if (loading) return <Centre><p className="muted">Connecting…</p></Centre>;

  if (session.state === "ok") return <Works profile={session.profile} />;
  if (session.state === "signedout") return <SignIn />;

  if (session.state === "noprofile") {
    return (
      <Problem
        title="Signed in, but you have no profile"
        detail={`Your login works — ${session.email} — but there is no matching row in the profiles table, so the app cannot tell what your role is.`}
        fix={`insert into profiles (id, full_name, role, active)\nvalues ('${session.uid}', 'Yash', 'owner', true)\non conflict (id) do update set role = 'owner', active = true;`}
      />
    );
  }

  if (session.state === "inactive") {
    return (
      <Problem
        title="Your login has been switched off"
        detail={`${session.profile.full_name} is marked inactive, so every policy refuses.`}
        fix={`update profiles set active = true where id = '${session.profile.id}';`}
      />
    );
  }

  return (
    <Problem
      title="Could not load your profile"
      detail={session.message || "Unknown error."}
      fix="Check the PowerShell window running npm run dev for the full error."
    />
  );
}

/* ------------------------------------------------------------------ */

function SignIn() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setNote("Contacting Supabase…");
    try {
      const { data, error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) setNote("Rejected: " + error.message);
      else if (data?.user) setNote("Accepted. Loading your profile…");
      else setNote("No error, but no user came back.");
    } catch (err) {
      setNote("Request failed: " + (err?.message || String(err)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Centre>
      <div className="mark">SNM WORKS</div>
      <div className="sub">Swadeshi Niwar Mills · Kanpur</div>

      <form onSubmit={submit} className="panel">
        <label>Email</label>
        <input type="email" value={email} autoComplete="username"
          onChange={(e) => setEmail(e.target.value)} required />

        <label>Password</label>
        <input type="password" value={password} autoComplete="current-password"
          onChange={(e) => setPassword(e.target.value)} required />

        <button type="submit" disabled={busy || !email || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>

        {/* whatever happens, it is said out loud on the page */}
        {note && <p className={note.startsWith("Accepted") ? "muted" : "error"}>{note}</p>}
      </form>
    </Centre>
  );
}

/* ------------------------------------------------------------------ */

function Works({ profile }) {
  const [perms, setPerms] = useState(null);
  const [permsError, setPermsError] = useState("");
  const [active, setActive] = useState(null);

  useEffect(() => {
    loadPermissions()
      .then((p) => {
        setPerms(p);
        const visible = modules.find((m) => m.visibleTo(p));
        if (visible) setActive(visible.key);
        else if (p.has("specifications.read")) setActive("specifications");
      })
      .catch((e) => setPermsError(e?.message || String(e)));
  }, []);

  const visibleModules = perms ? modules.filter((m) => m.visibleTo(perms)) : [];
  const showSpecs = perms?.has("specifications.read");

  return (
    <div className="app">
      <header>
        <div>
          <div className="mark small">SNM WORKS</div>
          <div className="sub">{profile.full_name} · {profile.role}</div>
        </div>
        <button className="ghost" onClick={() => supabase.auth.signOut()}>Sign out</button>
      </header>

      {permsError && (
        <div className="panel">
          <p className="error">Could not load your permissions: {permsError}</p>
        </div>
      )}

      {!permsError && !perms && <p className="muted">Loading your access…</p>}

      {perms && (
        <>
          <nav>
            {showSpecs && (
              <button className={active === "specifications" ? "on" : ""} onClick={() => setActive("specifications")}>
                Specifications
              </button>
            )}
            {visibleModules.map((m) => (
              <button key={m.key} className={active === m.key ? "on" : ""} onClick={() => setActive(m.key)}>
                {m.label}
              </button>
            ))}
          </nav>

          {!active && (
            <div className="panel">
              <p className="muted">
                Your roles carry no read access to any register yet. Ask whoever holds
                Chief Information or Chief People to check your roles in <b>user_roles</b>.
              </p>
            </div>
          )}

          {active === "specifications" && <Specifications perms={perms} />}

          {visibleModules
            .filter((m) => m.key === active)
            .map((m) => (
              <Register key={m.key} module={m} perms={perms} profile={profile} />
            ))}
        </>
      )}
    </div>
  );
}

function Problem({ title, detail, fix }) {
  return (
    <Centre>
      <div className="mark">SNM WORKS</div>
      <div className="sub">Something needs fixing</div>
      <div className="panel">
        <h2>{title}</h2>
        <p className="muted">{detail}</p>
        <p className="muted small">Run this in the Supabase SQL Editor:</p>
        <pre>{fix}</pre>
        <button className="small" onClick={() => window.location.reload()}>Try again</button>
        <button className="ghost inline" onClick={() => supabase.auth.signOut()}>Sign out</button>
      </div>
    </Centre>
  );
}

function Centre({ children }) {
  return <div className="centre"><div className="col">{children}</div></div>;
}
