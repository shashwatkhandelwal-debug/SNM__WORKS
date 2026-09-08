-- ============================================================================
-- 29_spec_uploads_standalone_and_source.sql
-- SNM Works — Standalone Specification Uploads & Bulk Textract Source Expansion
-- ============================================================================

-- 1. Allow standalone spec uploads without requiring an upfront commercial SKU linkage
ALTER TABLE spec_pdf_uploads ALTER COLUMN sku_id DROP NOT NULL;

-- 2. Expand source check constraint to include 'bulk_textract_batch'
-- Confirmed real constraint name: spec_pdf_uploads_source_check
ALTER TABLE spec_pdf_uploads DROP CONSTRAINT IF EXISTS spec_pdf_uploads_source_check;
ALTER TABLE spec_pdf_uploads ADD CONSTRAINT spec_pdf_uploads_source_check 
  CHECK (source IN ('web_upload', 'folder_watcher', 'bulk_textract_batch'));
