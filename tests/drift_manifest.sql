-- drift_manifest.sql: deterministic, read-only schema manifest of the `public` schema.
-- Run the SAME text on the local snm_test_db and on the Supabase project; compare the output lines.
--   fn|<name(args)>|<hash8>                      function definition (incl. SET/SECURITY DEFINER), extension-owned functions excluded
--   en|<enum>|<hash8>                            enum labels
--   tb|<table>|c=..|k=..|i=..|t=..|p=..          columns / constraints / indexes / triggers / policies (hash8, '-' = none)
-- Normalisation: CR removed, `public.` and `pg_catalog.` prefixes removed, whitespace collapsed, constraint and index NAMES ignored
-- (constraint/index definitions only), elements sorted before hashing. Column ORDER is ignored (sorted by name).
-- Works on PG16 and PG17. Read-only (SELECT only).

WITH raw AS (
  SELECT 'fn' AS kind, p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')' AS obj, 'def' AS part, pg_get_functiondef(p.oid) AS txt
  FROM pg_proc p
  WHERE p.pronamespace = 'public'::regnamespace AND p.prokind = 'f'
    AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')
  UNION ALL
  SELECT 'tb', c.relname::text, 'c',
         a.attname || ':' || format_type(a.atttypid, a.atttypmod) || ':' || a.attnotnull::text || ':' || a.attgenerated::text || ':' || a.attidentity::text || ':' || coalesce(pg_get_expr(d.adbin, d.adrelid), '')
  FROM pg_class c
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
  LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
  WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
  UNION ALL
  SELECT 'tb', c.relname::text, 'k', k.contype::text || ':' || pg_get_constraintdef(k.oid)
  FROM pg_class c JOIN pg_constraint k ON k.conrelid = c.oid
  WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
  UNION ALL
  SELECT 'tb', c.relname::text, 'i', regexp_replace(pg_get_indexdef(i.indexrelid), '^CREATE (UNIQUE )?INDEX \S+ ON', 'CREATE \1INDEX ON')
  FROM pg_class c JOIN pg_index i ON i.indrelid = c.oid
  WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
  UNION ALL
  SELECT 'tb', c.relname::text, 't', pg_get_triggerdef(g.oid)
  FROM pg_class c JOIN pg_trigger g ON g.tgrelid = c.oid AND NOT g.tgisinternal
  WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
  UNION ALL
  SELECT 'tb', p.tablename::text, 'p', p.policyname || '|' || p.cmd || '|' || p.roles::text || '|' || coalesce(p.qual, '') || '|' || coalesce(p.with_check, '')
  FROM pg_policies p WHERE p.schemaname = 'public'
  UNION ALL
  SELECT 'en', t.typname::text, 'labels', e.enumlabel
  FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
  WHERE t.typnamespace = 'public'::regnamespace
),
nz AS (
  SELECT kind, obj, part, regexp_replace(replace(replace(replace(txt, E'\r', ''), 'public.', ''), 'pg_catalog.', ''), '\s+', ' ', 'g') AS t
  FROM raw
),
agg AS (
  SELECT kind, obj, part, left(md5(string_agg(t, ';' ORDER BY t)), 8) AS h
  FROM nz GROUP BY kind, obj, part
),
lines AS (
  SELECT 'fn|' || obj || '|' || h AS line FROM agg WHERE kind = 'fn'
  UNION ALL
  SELECT 'en|' || obj || '|' || h FROM agg WHERE kind = 'en'
  UNION ALL
  SELECT 'tb|' || obj || '|c=' || coalesce(max(h) FILTER (WHERE part = 'c'), '-')
                      || '|k=' || coalesce(max(h) FILTER (WHERE part = 'k'), '-')
                      || '|i=' || coalesce(max(h) FILTER (WHERE part = 'i'), '-')
                      || '|t=' || coalesce(max(h) FILTER (WHERE part = 't'), '-')
                      || '|p=' || coalesce(max(h) FILTER (WHERE part = 'p'), '-')
  FROM agg WHERE kind = 'tb' GROUP BY obj
)
SELECT line FROM lines ORDER BY line COLLATE "C";
