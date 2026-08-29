-- ============================================================================
-- 09_marketing.sql — SKU Marketing Pipeline & Multi-Platform Publishing
-- Run in the Supabase SQL Editor.
-- ============================================================================

-- Add marketing, approval, and publication tracking columns to skus table
alter table public.skus
  add column if not exists post_status text not null default 'none'
    check (post_status in ('none', 'queued', 'approved', 'published', 'rejected')),
  add column if not exists post_draft jsonb,
  add column if not exists platform_results jsonb,
  add column if not exists post_approved_at timestamptz,
  add column if not exists post_published_at timestamptz,
  add column if not exists rejection_reason text,
  add column if not exists catalogue_visible boolean not null default false;

-- Indexes for marketing queue queries and public catalogue
create index if not exists idx_skus_post_status on public.skus(post_status);
create index if not exists idx_skus_catalogue on public.skus(status, catalogue_visible)
  where catalogue_visible = true;

comment on column public.skus.post_status is 'Marketing draft workflow state (none, queued, approved, published, rejected)';
comment on column public.skus.post_draft is 'Structured post payload and platform captions generated for owner review';
comment on column public.skus.platform_results is 'Platform publishing response receipts (LinkedIn, Instagram, Facebook, IndiaMart, TradeIndia)';
comment on column public.skus.catalogue_visible is 'Flag determining eligibility for public buyer catalogue and marketing queue';
