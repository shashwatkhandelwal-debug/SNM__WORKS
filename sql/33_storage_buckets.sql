-- =============================================================================
-- sql/33_storage_buckets.sql
-- Storage Buckets Creation and auth_can() Gated RLS Security on storage.objects
-- =============================================================================

-- 1. Initialize the 4 Storage Buckets
INSERT INTO storage.buckets (id, name, public)
VALUES 
  ('sku-images', 'sku-images', true),
  ('campaign-images', 'campaign-images', true),
  ('spec-docs', 'spec-docs', false),
  ('spec-parsed', 'spec-parsed', false)
ON CONFLICT (id) DO UPDATE SET public = EXCLUDED.public;

-- 2. Clean up legacy or permissive development policies on storage.objects
DROP POLICY IF EXISTS "Public can view sku-images" ON storage.objects;
DROP POLICY IF EXISTS "Authorized users can upload sku-images" ON storage.objects;
DROP POLICY IF EXISTS "Authorized users can update sku-images" ON storage.objects;
DROP POLICY IF EXISTS "authenticated users can read sku images" ON storage.objects;
DROP POLICY IF EXISTS "authenticated users can upload sku images" ON storage.objects;
DROP POLICY IF EXISTS "Authenticated users can update SKU images" ON storage.objects;

DROP POLICY IF EXISTS "Public can view campaign-images" ON storage.objects;
DROP POLICY IF EXISTS "Authorized users can upload campaign-images" ON storage.objects;
DROP POLICY IF EXISTS "Authorized users can update campaign-images" ON storage.objects;
DROP POLICY IF EXISTS "authenticated users can read campaign images" ON storage.objects;
DROP POLICY IF EXISTS "authenticated users can upload campaign images" ON storage.objects;
DROP POLICY IF EXISTS "Authenticated users can update campaign images" ON storage.objects;

DROP POLICY IF EXISTS "Authorized users can read spec-docs" ON storage.objects;
DROP POLICY IF EXISTS "Authorized users can upload spec-docs" ON storage.objects;
DROP POLICY IF EXISTS "Authorized users can update spec-docs" ON storage.objects;

DROP POLICY IF EXISTS "Authorized users can read spec-parsed" ON storage.objects;
DROP POLICY IF EXISTS "Authorized users can upload spec-parsed" ON storage.objects;
DROP POLICY IF EXISTS "Authorized users can update spec-parsed" ON storage.objects;

-- -----------------------------------------------------------------------------
-- 3. BUCKET: sku-images (PUBLIC READ, SKUS-GATED WRITE)
-- -----------------------------------------------------------------------------
CREATE POLICY "Public can view sku-images"
ON storage.objects FOR SELECT
USING (bucket_id = 'sku-images');

CREATE POLICY "Authorized users can upload sku-images"
ON storage.objects FOR INSERT
TO authenticated
WITH CHECK (
  bucket_id = 'sku-images'
  AND (public.auth_can('skus', 'create') OR public.auth_can('skus', 'update'))
);

CREATE POLICY "Authorized users can update sku-images"
ON storage.objects FOR UPDATE
TO authenticated
USING (
  bucket_id = 'sku-images'
  AND public.auth_can('skus', 'update')
)
WITH CHECK (
  bucket_id = 'sku-images'
  AND public.auth_can('skus', 'update')
);

-- -----------------------------------------------------------------------------
-- 4. BUCKET: campaign-images (PUBLIC READ, SKUS-GATED WRITE)
-- Matches campaigns table RLS: campaigns_insert & campaigns_update
-- -----------------------------------------------------------------------------
CREATE POLICY "Public can view campaign-images"
ON storage.objects FOR SELECT
USING (bucket_id = 'campaign-images');

CREATE POLICY "Authorized users can upload campaign-images"
ON storage.objects FOR INSERT
TO authenticated
WITH CHECK (
  bucket_id = 'campaign-images'
  AND public.auth_can('skus', 'create')
);

CREATE POLICY "Authorized users can update campaign-images"
ON storage.objects FOR UPDATE
TO authenticated
USING (
  bucket_id = 'campaign-images'
  AND public.auth_can('skus', 'update')
)
WITH CHECK (
  bucket_id = 'campaign-images'
  AND public.auth_can('skus', 'update')
);

-- -----------------------------------------------------------------------------
-- 5. BUCKET: spec-docs (PRIVATE — STRICT DEFENCE & PROPRIETARY SPECS)
-- -----------------------------------------------------------------------------
CREATE POLICY "Authorized users can read spec-docs"
ON storage.objects FOR SELECT
TO authenticated
USING (
  bucket_id = 'spec-docs'
  AND public.auth_can('specifications', 'read')
);

CREATE POLICY "Authorized users can upload spec-docs"
ON storage.objects FOR INSERT
TO authenticated
WITH CHECK (
  bucket_id = 'spec-docs'
  AND public.auth_can('specifications', 'create')
);

CREATE POLICY "Authorized users can update spec-docs"
ON storage.objects FOR UPDATE
TO authenticated
USING (
  bucket_id = 'spec-docs'
  AND public.auth_can('specifications', 'update')
)
WITH CHECK (
  bucket_id = 'spec-docs'
  AND public.auth_can('specifications', 'update')
);

-- -----------------------------------------------------------------------------
-- 6. BUCKET: spec-parsed (PRIVATE — INTERNAL EXTRACTED JSON SCHEMAS)
-- -----------------------------------------------------------------------------
CREATE POLICY "Authorized users can read spec-parsed"
ON storage.objects FOR SELECT
TO authenticated
USING (
  bucket_id = 'spec-parsed'
  AND public.auth_can('specifications', 'read')
);

CREATE POLICY "Authorized users can upload spec-parsed"
ON storage.objects FOR INSERT
TO authenticated
WITH CHECK (
  bucket_id = 'spec-parsed'
  AND public.auth_can('specifications', 'create')
);

CREATE POLICY "Authorized users can update spec-parsed"
ON storage.objects FOR UPDATE
TO authenticated
USING (
  bucket_id = 'spec-parsed'
  AND public.auth_can('specifications', 'update')
)
WITH CHECK (
  bucket_id = 'spec-parsed'
  AND public.auth_can('specifications', 'update')
);
