"""
scripts/sync_storage_to_supabase.py — One-time & Idempotent Supabase Storage Sync
=============================================================================
Uploads cached local files from static/uploads/ into Supabase Storage buckets:
  - sku-images
  - campaign-images
  - spec-docs
  - spec-parsed

Guarantees:
  1. Strictly restricted to the 4 specified buckets (never touches anything else).
  2. Safe to re-run (idempotent): Checks remote existence before upload;
     skips existing objects to prevent unexpected overwrites or duplicates.
  3. Safe authentication: Interactively authenticates using Supabase Auth (Owner JWT),
     ensuring credentials and tokens are never stored in files, logs, or command history.
  4. Supports --dry-run to preview actions without uploading anything.
=============================================================================
"""

import argparse
import getpass
import hashlib
import os
import sys
from typing import Dict, List, Optional, Tuple
import httpx

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import settings

# Strict target buckets and their mime types
TARGET_BUCKETS = {
    "sku-images": "image/jpeg",
    "campaign-images": "image/jpeg",
    "spec-docs": "application/pdf",
    "spec-parsed": "application/json",
}

UPLOAD_BASE_DIR = os.path.join(PROJECT_ROOT, "static", "uploads")


def compute_file_sha256(filepath: str) -> str:
    """Computes SHA-256 checksum of a local file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def authenticate_owner(email: str, password: str) -> str:
    """
    Authenticates against Supabase Auth to obtain an owner JWT token.
    Raises RuntimeError if authentication fails.
    """
    auth_url = f"{settings.supabase_url}/auth/v1/token?grant_type=password"
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Content-Type": "application/json",
    }
    payload = {"email": email.strip(), "password": password}

    resp = httpx.post(auth_url, json=payload, headers=headers, timeout=15.0)
    if resp.status_code != 200:
        raise RuntimeError(f"Authentication failed (HTTP {resp.status_code}): {resp.text}")

    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError("Supabase response did not contain an access_token.")
    return token


def list_local_bucket_files(bucket_id: str) -> List[Tuple[str, str]]:
    """
    Scans local static/uploads/{bucket_id}/ recursively.
    Returns list of (rel_path, abs_path).
    """
    bucket_dir = os.path.join(UPLOAD_BASE_DIR, bucket_id)
    if not os.path.isdir(bucket_dir):
        return []

    files = []
    for root, _, filenames in os.walk(bucket_dir):
        for fname in filenames:
            abs_path = os.path.join(root, fname)
            # relative path inside the bucket, using forward slashes
            rel_path = os.path.relpath(abs_path, bucket_dir).replace("\\", "/")
            files.append((rel_path, abs_path))
    return files


def remote_object_exists(
    client: httpx.Client,
    bucket_id: str,
    destination_path: str,
    token: str,
) -> bool:
    """
    Checks whether the object already exists in Supabase Storage.
    Returns True if exists (HTTP 200).
    """
    url = f"{settings.supabase_url}/storage/v1/object/info/{bucket_id}/{destination_path}"
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {token}",
    }
    resp = client.get(url, headers=headers)
    return resp.status_code == 200


def upload_file(
    client: httpx.Client,
    bucket_id: str,
    destination_path: str,
    abs_path: str,
    content_type: str,
    token: str,
) -> bool:
    """
    Uploads a file to Supabase Storage via REST API.
    """
    with open(abs_path, "rb") as f:
        file_bytes = f.read()

    url = f"{settings.supabase_url}/storage/v1/object/{bucket_id}/{destination_path}"
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {token}",
        "Content-Type": content_type,
        "x-upsert": "false",  # Never overwrite accidentally without explicit intent
    }
    resp = client.post(url, content=file_bytes, headers=headers, timeout=30.0)
    if resp.status_code in (200, 201):
        return True
    else:
        print(f"\n  [ERROR] Upload failed for {bucket_id}/{destination_path}: HTTP {resp.status_code} - {resp.text}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Idempotent sync of static/uploads to Supabase Storage.")
    parser.add_argument("--dry-run", action="store_true", help="Preview files to be uploaded without uploading.")
    parser.add_argument("--email", default="yashkhandelwal95@gmail.com", help="Supabase user email.")
    args = parser.parse_args()

    print("=================================================================")
    print("  SNM Works — Supabase Storage Sync Utility")
    print(f"  Target: {settings.supabase_url}")
    print(f"  Local uploads: {UPLOAD_BASE_DIR}")
    print(f"  Mode: {'DRY RUN (preview only)' if args.dry_run else 'LIVE SYNC'}")
    print("=================================================================")

    # 1. Inspect local files across the 4 buckets
    plan: Dict[str, List[Tuple[str, str]]] = {}
    total_files = 0
    for bucket in TARGET_BUCKETS:
        files = list_local_bucket_files(bucket)
        plan[bucket] = files
        total_files += len(files)
        print(f"  Found {len(files):>4} files in local cache for bucket: '{bucket}'")

    if total_files == 0:
        print("\nNo files found in local static/uploads/ to sync.")
        return

    print(f"\nTotal local files to verify/sync: {total_files}")

    # 2. Authenticate
    token = ""
    if not args.dry_run:
        print(f"\nAuthenticating as: {args.email}")
        password = os.environ.get("SUPABASE_OWNER_PASSWORD")
        if not password:
            password = getpass.getpass("Enter Supabase password for owner: ")
        try:
            token = authenticate_owner(args.email, password)
            print("✓ Successfully authenticated with Supabase Auth.")
        except Exception as exc:
            print(f"Authentication failed: {exc}", file=sys.stderr)
            sys.exit(1)

    # 3. Process each bucket
    stats = {"skipped": 0, "uploaded": 0, "errors": 0}
    with httpx.Client(timeout=30.0) as client:
        for bucket, files in plan.items():
            content_type = TARGET_BUCKETS[bucket]
            print(f"\nProcessing bucket: [{bucket}] ({len(files)} files)")

            for rel_path, abs_path in files:
                file_size = os.path.getsize(abs_path)

                if args.dry_run:
                    print(f"  [DRY-RUN] Would check/sync {bucket}/{rel_path} ({file_size} bytes)")
                    stats["skipped"] += 1
                    continue

                # Check if already exists in Supabase
                try:
                    exists = remote_object_exists(client, bucket, rel_path, token)
                except Exception as exc:
                    print(f"  [WARN] Info check failed for {rel_path}: {exc}")
                    exists = False

                if exists:
                    print(f"  [SKIP] Exists on remote: {bucket}/{rel_path}")
                    stats["skipped"] += 1
                    continue

                # Upload
                print(f"  [UPLOAD] Syncing {bucket}/{rel_path} ({file_size} bytes)...", end="", flush=True)
                ok = upload_file(client, bucket, rel_path, abs_path, content_type, token)
                if ok:
                    print(" OK")
                    stats["uploaded"] += 1
                else:
                    stats["errors"] += 1

    print("\n=================================================================")
    print("  Sync Summary:")
    print(f"    Uploaded: {stats['uploaded']}")
    print(f"    Skipped (already synced): {stats['skipped']}")
    print(f"    Errors:   {stats['errors']}")
    print("=================================================================")


if __name__ == "__main__":
    main()
