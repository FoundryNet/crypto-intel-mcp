-- crypto-intel-mcp schema (standalone crypto-intel Supabase project). Idempotent.
--
-- Tables:
--   crypto_prices      — current price snapshot, one row per coin (cap-desc reads)
--   crypto_ohlcv       — historical daily price/volume, (coin_id,date)
--   defi_protocols     — DeFiLlama protocol TVL cache, one row per protocol name
--   fear_greed         — daily crypto Fear & Greed index, one row per date
--   crypto_query_usage — per-agent/day free-tier counter (+ crypto_claim_free_query)
--   crypto_payments    — verified x402 payments ledger (double-spend guard)
--   daily_briefs       — curated daily brief (+ increment_brief_purchase)

-- ── crypto_prices (current snapshot) ──────────────────────────────────────────
create table if not exists crypto_prices (
  coin_id         text primary key,
  symbol          text,
  name            text,
  price_usd       numeric,
  change_24h_pct  numeric,
  volume_24h      numeric,
  market_cap      numeric,
  last_updated    text,
  updated_at      timestamptz not null default now()
);
create index if not exists idx_crypto_prices_cap on crypto_prices (market_cap desc nulls last);
create index if not exists idx_crypto_prices_symbol on crypto_prices (symbol);

-- ── crypto_ohlcv (historical daily) ───────────────────────────────────────────
create table if not exists crypto_ohlcv (
  coin_id  text not null,
  date     date not null,
  price    numeric,
  volume   numeric,
  primary key (coin_id, date)
);
create index if not exists idx_crypto_ohlcv_coin_date on crypto_ohlcv (coin_id, date desc);

-- ── defi_protocols (DeFiLlama TVL cache) ──────────────────────────────────────
create table if not exists defi_protocols (
  name        text primary key,
  category    text,
  tvl         numeric,
  change_1d   numeric,
  change_7d   numeric,
  chain       text,
  updated_at  timestamptz not null default now()
);
create index if not exists idx_defi_protocols_tvl on defi_protocols (tvl desc nulls last);

-- ── fear_greed (daily index) ──────────────────────────────────────────────────
create table if not exists fear_greed (
  date            date primary key,
  value           integer,
  classification  text,
  updated_at      timestamptz not null default now()
);

-- ── crypto_query_usage (free-tier counter) ────────────────────────────────────
create table if not exists crypto_query_usage (
  agent_key   text not null,
  day         date not null,
  count       integer not null default 0,
  updated_at  timestamptz not null default now(),
  primary key (agent_key, day)
);

-- ── crypto_payments (x402 ledger / double-spend guard) ─────────────────────────
create table if not exists crypto_payments (
  tx_signature  text primary key,
  intent        text,
  agent_key     text,
  tool          text,
  amount_usdc   numeric,
  payer_wallet  text,
  recipient     text,
  status        text,
  block_time    bigint,
  created_at    timestamptz not null default now()
);

-- ── daily_briefs ──────────────────────────────────────────────────────────────
create table if not exists daily_briefs (
  brief_date        date primary key,
  brief_data        jsonb not null,
  signal_count      integer not null default 0,
  attestation_hash  text,
  purchase_count    integer not null default 0,
  expires_at        timestamptz,
  created_at        timestamptz not null default now()
);

create or replace function crypto_claim_free_query(p_agent_key text, p_day date, p_cap integer)
returns jsonb language plpgsql as $$
declare cur integer; ok boolean;
begin
  insert into crypto_query_usage (agent_key, day, count, updated_at)
  values (p_agent_key, p_day, 0, now())
  on conflict (agent_key, day) do nothing;

  select count into cur from crypto_query_usage
    where agent_key = p_agent_key and day = p_day for update;

  if cur < p_cap then
    update crypto_query_usage set count = count + 1, updated_at = now()
      where agent_key = p_agent_key and day = p_day;
    ok := true; cur := cur + 1;
  else
    ok := false;
  end if;
  return jsonb_build_object('allowed', ok, 'count', cur, 'cap', p_cap);
end;
$$;

create or replace function increment_brief_purchase(p_brief_date date)
returns void language sql as $$
  update daily_briefs set purchase_count = purchase_count + 1 where brief_date = p_brief_date;
$$;
