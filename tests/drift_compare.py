"""Schema drift check (read-only): compare the local snm_test_db manifest with the production manifest.

Usage (from the repo root, after `python tests/migrate_local.py`):
    python tests/drift_compare.py

It runs tests/drift_manifest.sql against the LOCAL database only, writes tests/local_manifest.txt, and prints:
  - objects only in production / only in local
  - objects present in both whose hash differs (for tables: which component differs)
It never connects to Supabase and never modifies any database.
"""
import asyncio
import hashlib
import os
import pathlib
import sys

import asyncpg

HERE = pathlib.Path(__file__).resolve().parent
PROD_FILE = HERE / "production_manifest_2026-09-22.txt"
PROD_MD5 = "83d6cbfaab1fc2c8c787b36ed0803372"  # md5 of the 75 lines joined by "\n", computed by the database
SQL_FILE = HERE / "drift_manifest.sql"
LOCAL_OUT = HERE / "local_manifest.txt"
DSN = os.environ.get("LOCAL_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/snm_test_db")
PARTS = {"c": "columns", "k": "constraints", "i": "indexes", "t": "triggers", "p": "policies"}


def load_lines(path: pathlib.Path):
    return [ln for ln in path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n") if ln.strip()]


def parse(lines):
    out = {}
    for ln in lines:
        kind, name, *rest = ln.split("|")
        out[(kind, name)] = rest
    return out


async def local_lines():
    conn = await asyncpg.connect(DSN)
    try:
        rows = await conn.fetch(SQL_FILE.read_text(encoding="utf-8"))
    finally:
        await conn.close()
    return [r["line"] for r in rows]


def main():
    prod_lines = load_lines(PROD_FILE)
    got = hashlib.md5("\n".join(prod_lines).encode()).hexdigest()
    if got != PROD_MD5:
        sys.exit(f"ERROR: {PROD_FILE.name} was altered (md5 {got} != {PROD_MD5}). Restore the original file.")
    loc_lines = asyncio.run(local_lines())
    LOCAL_OUT.write_text("\n".join(loc_lines) + "\n", encoding="utf-8")

    prod, loc = parse(prod_lines), parse(loc_lines)
    only_prod = sorted(set(prod) - set(loc))
    only_loc = sorted(set(loc) - set(prod))
    both = sorted(set(prod) & set(loc))
    differs = []
    for key in both:
        if prod[key] == loc[key]:
            continue
        if key[0] == "tb":
            diff_parts = []
            for p, l in zip(prod[key], loc[key]):
                if p != l:
                    diff_parts.append(f"{PARTS[p.split('=')[0]]} (prod {p.split('=')[1]} / local {l.split('=')[1]})")
            differs.append((key, "; ".join(diff_parts)))
        else:
            differs.append((key, f"prod {prod[key][0]} / local {loc[key][0]}"))

    print(f"production manifest: {len(prod_lines)} lines (md5 verified)")
    print(f"local manifest     : {len(loc_lines)} lines -> {LOCAL_OUT.name}")
    print(f"\n== only in PRODUCTION ({len(only_prod)}) ==")
    for k in only_prod:
        print(f"  {k[0]} {k[1]}")
    print(f"\n== only in LOCAL ({len(only_loc)}) ==")
    for k in only_loc:
        print(f"  {k[0]} {k[1]}")
    print(f"\n== DIFFER ({len(differs)}) ==")
    for k, d in differs:
        print(f"  {k[0]} {k[1]}: {d}")
    print(f"\nidentical: {len(both) - len(differs)} of {len(both)} objects present in both")


if __name__ == "__main__":
    main()
