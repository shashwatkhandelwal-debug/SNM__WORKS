import asyncio
import hashlib
import json
from pathlib import Path
import asyncpg

async def verify():
    conn = await asyncpg.connect('postgresql://postgres@127.0.0.1:5433/snm_test_db')
    samples = [
        'TAPE NYLON 21MM 600 KG.pdf',
        'RIBBON 50MM UD.pdf',
        'CREEPE ELASTIC 25MM.pdf',
        'FABRIC 300 GSM RIPSTOP.pdf',
        'NYLON 6 FABRIC 50 GSM.pdf',
        'TAPE NYLON UVR HZP.pdf',
    ]
    
    print('=' * 80)
    print('  1. SHA-256 HASH VERIFICATION (DB vs Staged JSON)')
    print('=' * 80)
    for s in samples:
        row = await conn.fetchrow(
            "SELECT id, original_filename, parsed_json_sha256, parsed_json_path FROM spec_pdf_uploads WHERE original_filename = $1",
            s
        )
        if row:
            stg_p = Path(r"C:\Users\ASUS\Downloads\bulk_spec_import_batch2\parsed_staging") / (Path(s).stem + '.json')
            if not stg_p.exists():
                stg_p = Path(r"C:\Users\ASUS\Downloads\bulk_spec_import\parsed_staging") / (Path(s).stem + '.json')
            stg_bytes = stg_p.read_bytes()
            stg_sha = hashlib.sha256(stg_bytes).hexdigest()
            db_sha = row['parsed_json_sha256']
            req_cnt = len(json.loads(stg_bytes.decode('utf-8')).get('requirements', []))
            match_status = "MATCH (VERIFIED)" if db_sha == stg_sha else "MISMATCH"
            print(f"File: {s}")
            print(f"  DB SHA256:     {db_sha}")
            print(f"  Staged SHA256: {stg_sha}")
            print(f"  Status:        {match_status} | Extracted Requirements: {req_cnt}\n")
        else:
            print(f"File: {s} -> NOT FOUND IN DB\n")
    
    print('=' * 80)
    print('  2. ZERO-REQUIREMENT DOCUMENTS INVENTORY & ROOT CAUSE INSPECTION')
    print('=' * 80)
    zero_docs = [
        'ELASTIC TAPE 10MM.pdf',
        'TAPE NYLON THICK 25MM OG.pdf',
        'TAPE NYLON THIN 13MM OG.pdf',
    ]
    for zd in zero_docs:
        row = await conn.fetchrow(
            "SELECT id, original_filename, parsed_json_path FROM spec_pdf_uploads WHERE original_filename = $1",
            zd
        )
        stg_p = Path(r"C:\Users\ASUS\Downloads\bulk_spec_import_batch2\parsed_staging") / (Path(zd).stem + '.json')
        if not stg_p.exists():
            stg_p = Path(r"C:\Users\ASUS\Downloads\bulk_spec_import\parsed_staging") / (Path(zd).stem + '.json')
        
        print(f"\nDocument: {zd}")
        if stg_p.exists():
            data = json.loads(stg_p.read_text(encoding='utf-8'))
            print(f"  Spec Title/No: {data.get('specification', {}).get('title')} / {data.get('specification', {}).get('spec_no')}")
            print(f"  Notes:         {data.get('specification', {}).get('notes')}")
            print(f"  Requirements:  {len(data.get('requirements', []))}")
            print(f"  Variants:      {len(data.get('variants', []))}")
            
            # Check raw textract cache
            stem = Path(zd).stem
            cache_p = Path('raw_textract_cache') / f"{stem}.json"
            if not cache_p.exists():
                cache_p = Path(r"C:\Users\ASUS\Downloads\bulk_spec_import_batch2\raw_textract_cache") / f"{stem}.json"
            if cache_p.exists():
                raw = json.loads(cache_p.read_text(encoding='utf-8'))
                lines = [b.get('Text') for b in raw.get('Blocks', []) if b.get('BlockType') == 'LINE']
                print(f"  Raw OCR Total Lines: {len(lines)}")
                print(f"  Raw OCR Text Content:")
                for l in lines:
                    print(f"    | {l}")
            else:
                print("  No raw textract cache found")

    total_uploads = await conn.fetchval("SELECT count(*) FROM spec_pdf_uploads;")
    print(f"\nTotal spec_pdf_uploads in DB: {total_uploads}")
    await conn.close()

if __name__ == '__main__':
    asyncio.run(verify())
