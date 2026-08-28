/**
 * Specification browser and editor — reads the generic spec model described
 * in CLAUDE.md (05_specifications.sql, spec_check_plan()) and MIL-W-4088K's
 * 60 variants / 560 requirements (06_load_mil4088.sql).
 *
 * Deliberately left as a placeholder: those two migration files don't exist
 * on disk, and 01_schema.sql on this machine still has the flat
 * mil_w_4088_types table that CLAUDE.md says was scrapped in favour of the
 * generic model. Building this screen against a guessed schema risks it
 * being wrong against the live database — safer to wait for the table/
 * function list back from the database than to transcribe from memory.
 */
export default function Specifications() {
  return (
    <div className="panel">
      <h2>Specifications</h2>
      <p className="muted">
        This screen isn't built yet. It needs to match the real specification
        tables in the live database, and that record doesn't exist as a file
        anywhere on this machine yet — it'll be finished as soon as that's
        confirmed.
      </p>
    </div>
  );
}
