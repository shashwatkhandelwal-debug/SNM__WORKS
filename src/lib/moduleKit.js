import { supabase } from "./supabase";

/**
 * Describes one register: a table, what a list row looks like, what a form
 * looks like, and which permission module governs it. Register.jsx renders
 * any Module without knowing anything about the table underneath it.
 *
 * Field shape:
 *   key          column name
 *   label        shown on screen
 *   type         'text' | 'number' | 'date' | 'select' | 'checkbox' | 'textarea'
 *   required     form validation only — RLS is the real enforcement
 *   default      value (or zero-arg function) used when starting a new row
 *   options      static array of strings, for type 'select'
 *   fk           { table, value, label } — loads a dropdown from another table
 *                and resolves the id to a readable label in the list view
 *   auto         true = filled with the signed-in profile id, hidden from the form
 *   autoOn       'create' | 'both' — when `auto` applies (default 'create')
 *   readOnlyOnEdit  true = editable when creating, locked once the row exists
 *   computed     true = a database-generated value; never sent on write
 *   hideInForm   true = never shown in the add/edit form
 *   hideInList   true = never shown as a table column
 *   mono         render in the monospace numeral font
 *   align        'right' for numbers
 *   verdict      true = render PASS green / FAIL red, matching qc_checks.verdict
 */
export class Module {
  constructor({
    key,
    label,
    table,
    permissionModule,
    idField = "id",
    orderBy = idField,
    ascending = false,
    fields,
  }) {
    this.key = key;
    this.label = label;
    this.table = table;
    this.permissionModule = permissionModule;
    this.idField = idField;
    this.orderBy = orderBy;
    this.ascending = ascending;
    this.fields = fields;
  }

  can(perms, action) {
    return perms.has(`${this.permissionModule}.${action}`);
  }

  visibleTo(perms) {
    return this.can(perms, "read");
  }

  listFields() {
    return this.fields.filter((f) => !f.hideInList);
  }

  formFields() {
    return this.fields.filter((f) => !f.hideInForm && !f.computed);
  }

  blankForm() {
    const out = {};
    for (const f of this.formFields()) {
      if (f.auto) continue;
      out[f.key] = typeof f.default === "function" ? f.default() : f.default ?? "";
    }
    return out;
  }

  formFromRow(row) {
    const out = {};
    for (const f of this.formFields()) {
      if (f.auto) continue;
      out[f.key] = row[f.key] ?? "";
    }
    return out;
  }

  async list() {
    const { data, error } = await supabase
      .from(this.table)
      .select("*")
      .order(this.orderBy, { ascending: this.ascending });
    if (error) throw error;
    return data;
  }

  async create(values, profileId) {
    const row = this.toRow(values);
    for (const f of this.fields) {
      if (f.auto && (f.autoOn ?? "create") !== "update") row[f.key] = profileId;
    }
    const { data, error } = await supabase.from(this.table).insert(row).select().single();
    if (error) throw error;
    return data;
  }

  async update(id, values, profileId) {
    const row = this.toRow(values, { isEdit: true });
    for (const f of this.fields) {
      if (f.auto && f.autoOn === "both") row[f.key] = profileId;
    }
    const { data, error } = await supabase
      .from(this.table)
      .update(row)
      .eq(this.idField, id)
      .select()
      .single();
    if (error) throw error;
    return data;
  }

  toRow(values, { isEdit = false } = {}) {
    const row = {};
    for (const f of this.formFields()) {
      if (f.auto) continue;
      if (isEdit && f.readOnlyOnEdit) continue;
      let v = values[f.key];
      if (v === "") v = null;
      if (f.type === "number" && v !== null && v !== undefined) v = Number(v);
      row[f.key] = v;
    }
    return row;
  }
}

/**
 * Every permission the signed-in user holds, as a Set of "module.action"
 * strings — e.g. "costing.read". Built from the role tables in 07_roles.sql
 * (my_roles + role_permissions), not from profiles.role. Used only to decide
 * what the UI offers; the database enforces the real rule regardless.
 */
export async function loadPermissions() {
  const { data: roleRows, error: roleErr } = await supabase.rpc("my_roles");
  if (roleErr) throw roleErr;
  const roles = (roleRows ?? []).map((r) => (typeof r === "string" ? r : r.my_roles));
  if (roles.length === 0) return new Set();

  const { data: permRows, error: permErr } = await supabase
    .from("role_permissions")
    .select("module, action")
    .in("role_code", roles);
  if (permErr) throw permErr;

  return new Set(permRows.map((p) => `${p.module}.${p.action}`));
}

export async function fkOptions(fk) {
  const { data, error } = await supabase.from(fk.table).select(`${fk.value}, ${fk.label}`);
  if (error) throw error;
  const map = new Map();
  for (const row of data) map.set(row[fk.value], row[fk.label]);
  return map;
}
