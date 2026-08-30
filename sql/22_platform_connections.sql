-- ============================================================================
-- 22_platform_connections.sql — Social Media & Platform API Token Storage
-- Run in the Supabase SQL Editor.
-- ============================================================================

-- One-time setup: run this in PowerShell to generate your encryption key:
-- python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
-- Copy the output into .env as ENCRYPTION_KEY=

create table if not exists public.platform_connections (
  id                      uuid primary key default gen_random_uuid(),
  user_id                 uuid not null references public.profiles(id) on delete cascade,
  platform                text not null check (platform in ('linkedin', 'instagram', 'facebook', 'indiamart', 'tradeindia')),
  account_name            text,
  account_id              text,            -- e.g. urn:li:organization:12345678
  access_token_encrypted  text not null,   -- Fernet AES-encrypted token or API key
  refresh_token_encrypted text,            -- Optional encrypted refresh token
  token_expires_at        timestamptz,
  scopes                  text[],
  is_active               boolean not null default true,
  metadata                jsonb,           -- organization details, vanity name, api endpoints
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),
  constraint uq_user_platform unique (user_id, platform)
);

create index if not exists idx_platform_connections_user on public.platform_connections(user_id);
create index if not exists idx_platform_connections_platform on public.platform_connections(platform);

alter table public.platform_connections enable row level security;

create policy "Users manage own platform connections"
on public.platform_connections
for all
to authenticated
using (user_id = auth.uid())
with check (user_id = auth.uid());

comment on table public.platform_connections is
  'Encrypted OAuth tokens and API keys for social publishing and B2B portal integrations.';
