-- ============================================================================
-- 10_campaigns.sql — Promotional Marketing Campaigns & Event Posts
-- Run in the Supabase SQL Editor.
-- ============================================================================

create table if not exists public.campaigns (
  id                  uuid primary key default gen_random_uuid(),
  occasion            text,
  headline            text not null,
  body                text not null,
  featured_sku_id     uuid references public.skus(id) on delete set null,
  platforms           text[] not null default array['linkedin','instagram','facebook','indiamart','tradeindia'],
  scheduled_at        timestamptz not null default now(),
  post_status         text not null default 'queued'
    check (post_status in ('draft', 'queued', 'approved', 'published', 'rejected')),
  post_draft          jsonb,
  platform_results    jsonb,
  post_approved_at    timestamptz,
  post_published_at   timestamptz,
  rejection_reason    text,
  created_at          timestamptz not null default now(),
  created_by          uuid references public.profiles(id)
);

create index if not exists idx_campaigns_post_status on public.campaigns(post_status);

comment on table public.campaigns is 'Promotional, holiday, and company announcement marketing campaigns';
comment on column public.campaigns.post_status is 'Campaign approval workflow state (draft, queued, approved, published, rejected)';
comment on column public.campaigns.platform_results is 'Publishing response receipts per platform';
