import { useEffect, useMemo, useState } from "react";
import { fkOptions } from "../lib/moduleKit";

export default function Register({ module, perms, profile }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  const [fkMaps, setFkMaps] = useState({});
  const [mode, setMode] = useState("list"); // 'list' | 'new' | 'edit'
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState({});
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  const canCreate = module.can(perms, "create");
  const canUpdate = module.can(perms, "update");

  useEffect(() => {
    load();
    loadFks();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [module]);

  async function load() {
    setRows(null);
    try {
      setRows(await module.list());
    } catch (e) {
      setError(e?.message || String(e));
    }
  }

  async function loadFks() {
    const fkFields = module.fields.filter((f) => f.fk);
    const entries = await Promise.all(
      fkFields.map(async (f) => [f.key, await fkOptions(f.fk)])
    );
    setFkMaps(Object.fromEntries(entries));
  }

  function startNew() {
    setForm(module.blankForm());
    setEditingId(null);
    setFormError("");
    setMode("new");
  }

  function startEdit(row) {
    setForm(module.formFromRow(row));
    setEditingId(row[module.idField]);
    setFormError("");
    setMode("edit");
  }

  function setField(key, value) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function submit(e) {
    e.preventDefault();
    setSaving(true);
    setFormError("");
    try {
      if (mode === "new") await module.create(form, profile.id);
      else await module.update(editingId, form, profile.id);
      setMode("list");
      await load();
    } catch (e) {
      setFormError(e?.message || String(e));
    } finally {
      setSaving(false);
    }
  }

  const listFields = useMemo(() => module.listFields(), [module]);

  if (mode === "new" || mode === "edit") {
    return (
      <div className="panel">
        <div className="rowhead">
          <h2>{mode === "new" ? `New ${module.label}` : `Edit ${module.label}`}</h2>
        </div>
        <RegisterForm
          module={module}
          form={form}
          setField={setField}
          fkMaps={fkMaps}
          isEdit={mode === "edit"}
        />
        {formError && <p className="error">{formError}</p>}
        <button onClick={submit} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button type="button" className="ghost inline" onClick={() => setMode("list")}>
          Cancel
        </button>
      </div>
    );
  }

  return (
    <div className="panel">
      <div className="rowhead">
        <h2>{module.label}</h2>
        {canCreate && (
          <button className="small" onClick={startNew}>
            + Add
          </button>
        )}
      </div>

      {error && <p className="error">{error}</p>}
      {!error && rows === null && <p className="muted">Loading…</p>}
      {!error && rows && rows.length === 0 && <p className="muted">Nothing recorded yet.</p>}

      {!error && rows && rows.length > 0 && (
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                {listFields.map((f) => (
                  <th key={f.key} className={f.align === "right" ? "right" : ""}>
                    {f.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row[module.idField]}
                  className={canUpdate ? "clickable" : ""}
                  onClick={() => canUpdate && startEdit(row)}
                >
                  {listFields.map((f) => (
                    <td
                      key={f.key}
                      className={[f.align === "right" ? "right" : "", f.mono ? "mono" : ""].join(" ")}
                    >
                      <Cell field={f} value={row[f.key]} fkMaps={fkMaps} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Cell({ field, value, fkMaps }) {
  if (value === null || value === undefined || value === "") return <span className="muted">—</span>;
  if (field.fk) return fkMaps[field.key]?.get(value) ?? <span className="muted">—</span>;
  if (field.type === "checkbox") return value ? "Yes" : "No";
  if (field.verdict) return <span className={value === "PASS" ? "ok" : value === "FAIL" ? "bad" : "muted"}>{value}</span>;
  return String(value);
}

function RegisterForm({ module, form, setField, fkMaps, isEdit }) {
  const fields = module.formFields().filter((f) => !f.auto);
  const sections = [];
  const seen = new Set();
  for (const f of fields) {
    const s = f.section || null;
    if (!seen.has(s)) {
      seen.add(s);
      sections.push(s);
    }
  }

  return (
    <>
      {sections.map((section) => (
        <div key={section || "_main"} className={section ? "formsection" : undefined}>
          {section && <div className="formsection-title">{section}</div>}
          <div className="grid2">
            {fields
              .filter((f) => (f.section || null) === section)
              .map((f) => (
                <Field
                  key={f.key}
                  field={f}
                  value={form[f.key]}
                  onChange={(v) => setField(f.key, v)}
                  fkMap={fkMaps[f.key]}
                  disabled={isEdit && f.readOnlyOnEdit}
                />
              ))}
          </div>
        </div>
      ))}
    </>
  );
}

function Field({ field, value, onChange, fkMap, disabled }) {
  const wide = field.type === "textarea";

  if (field.type === "checkbox") {
    return (
      <label className="check">
        <input
          type="checkbox"
          checked={!!value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
        />
        {field.label}
      </label>
    );
  }

  return (
    <div className={wide ? "wide" : undefined}>
      <label>
        {field.label}
        {field.required ? " *" : ""}
      </label>
      {field.type === "textarea" && (
        <textarea
          rows={3}
          value={value ?? ""}
          disabled={disabled}
          required={field.required}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      {field.fk && (
        <select value={value ?? ""} disabled={disabled} required={field.required} onChange={(e) => onChange(e.target.value)}>
          <option value="">—</option>
          {fkMap &&
            [...fkMap.entries()].map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
        </select>
      )}
      {!field.fk && field.type === "select" && (
        <select value={value ?? ""} disabled={disabled} required={field.required} onChange={(e) => onChange(e.target.value)}>
          <option value="">—</option>
          {field.options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      )}
      {!field.fk && (field.type === "text" || field.type === "number" || field.type === "date") && (
        <input
          type={field.type}
          value={value ?? ""}
          disabled={disabled}
          required={field.required}
          className={field.mono ? "mono" : undefined}
          style={field.align === "right" ? { textAlign: "right" } : undefined}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </div>
  );
}
