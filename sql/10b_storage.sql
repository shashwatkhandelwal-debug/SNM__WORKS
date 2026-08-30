-- ============================================================================
-- 10_storage.sql — Storage RLS Policies for SKU Photos and Campaign Graphics
-- Run in the Supabase SQL Editor AFTER creating the buckets in the dashboard.
-- ============================================================================

-- 1. Storage RLS Policies for 'sku-images' bucket
create policy "Authenticated users can upload SKU images"
on storage.objects for insert
to authenticated
with check (bucket_id = 'sku-images');

create policy "Authenticated users can update SKU images"
on storage.objects for update
to authenticated
using (bucket_id = 'sku-images');

create policy "Authenticated users can read SKU images"
on storage.objects for select
to authenticated
using (bucket_id = 'sku-images');

-- 2. Storage RLS Policies for 'campaign-images' bucket
create policy "Authenticated users can upload campaign images"
on storage.objects for insert
to authenticated
with check (bucket_id = 'campaign-images');

create policy "Authenticated users can update campaign images"
on storage.objects for update
to authenticated
using (bucket_id = 'campaign-images');

create policy "Authenticated users can read campaign images"
on storage.objects for select
to authenticated
using (bucket_id = 'campaign-images');
