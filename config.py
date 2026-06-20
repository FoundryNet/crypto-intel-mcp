"""Env-driven configuration for crypto-intel-mcp.

A crypto market-intelligence MCP server. `price` and `market_overview` are FREE
(the loss leader — massive volume, every call surfaces the FoundryNet network);
`price_history`, `whale_alerts`, `defi_overview` and `anomaly_scan` are paid via
x402. Prices, DeFi TVL, OHLCV history and the Fear & Greed index are aggregated
every 15 minutes from keyless sources (CoinGecko + DeFiLlama + alternative.me) and
cached in its OWN standalone Supabase project. Part of the FoundryNet Data Network.

Required to be useful:
  SUPABASE_URL, SUPABASE_SERVICE_KEY   the standalone crypto-intel Supabase project.
Optional:
  PORT, REQUEST_TIMEOUT
  X402_ENABLED            "true" arms the paywall on the paid tools (DEFAULT true)
  SOLANA_WALLET / PAYMENT_RECIPIENT / PAYMENT_VERIFY_RPC / PAYMENT_USDC_MINT /
  PAYMENT_EXPIRY_SECONDS
  FREE_TIER_DAILY         free PAID-tool queries/day per agent, default 50
  AGG_INTERVAL_MINUTES    market refresh cadence, default 15
  OHLCV_BACKFILL_DAYS     days of OHLCV history to backfill on first run, default 90
  COINGECKO_API_KEY       optional CoinGecko demo key (x-cg-demo-api-key header)
  PRICE_PRICE_HISTORY     default 0.01
  PRICE_WHALE_ALERTS      default 0.01
  PRICE_DEFI_OVERVIEW     default 0.01
  PRICE_ANOMALY_SCAN      default 0.02
  PRICE_DAILY_BRIEF       default 15
  FNET_API_KEY            fleet bearer for free internal sibling calls
  PUBLIC_MCP_URL
"""
from __future__ import annotations

import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _flag(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").strip().lower() in ("1", "true", "yes", "on")


# ── Standalone crypto-intel Supabase project ─────────────────────────────────
SUPABASE_URL         = _env("SUPABASE_URL", "https://dhugsfukubgkhtfuybmi.supabase.co").rstrip("/")
SUPABASE_SERVICE_KEY = _env("SUPABASE_SERVICE_KEY")

PORT            = int(_env("PORT", "8080"))
REQUEST_TIMEOUT = int(_env("REQUEST_TIMEOUT", "30"))

# ── Market aggregation ───────────────────────────────────────────────────────
AGG_INTERVAL_MINUTES = int(_env("AGG_INTERVAL_MINUTES", "15"))
OHLCV_BACKFILL_DAYS  = int(_env("OHLCV_BACKFILL_DAYS", "90"))
# Number of top coins (by market cap) to backfill OHLCV history for on cold start.
OHLCV_BACKFILL_COINS = int(_env("OHLCV_BACKFILL_COINS", "30"))

# Optional CoinGecko demo key — sent as the x-cg-demo-api-key header when set.
COINGECKO_API_KEY = _env("COINGECKO_API_KEY").strip()

# ── x402 pay-per-query gate (paid tools only) ────────────────────────────────
X402_ENABLED      = _flag("X402_ENABLED", True)
SOLANA_WALLET     = _env("SOLANA_WALLET", "wUumjWWvtFEr69qkTw3wHNVQVxLA8DTyJSyVgGmLThd")
PAYMENT_RECIPIENT = _env("PAYMENT_RECIPIENT", SOLANA_WALLET).strip()
PAYMENT_VERIFY_RPC = _env("PAYMENT_VERIFY_RPC", "https://api.mainnet-beta.solana.com").rstrip("/")
PAYMENT_USDC_MINT  = _env("PAYMENT_USDC_MINT", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v").strip()
PAYMENT_EXPIRY_SECONDS = int(_env("PAYMENT_EXPIRY_SECONDS", "300"))

FREE_TIER_DAILY = int(_env("FREE_TIER_DAILY", "50"))

PRICE_PRICE_HISTORY = float(_env("PRICE_PRICE_HISTORY", "0.01"))
PRICE_WHALE_ALERTS  = float(_env("PRICE_WHALE_ALERTS", "0.01"))
PRICE_DEFI_OVERVIEW = float(_env("PRICE_DEFI_OVERVIEW", "0.01"))
PRICE_ANOMALY_SCAN  = float(_env("PRICE_ANOMALY_SCAN", "0.02"))
PRICE_DAILY_BRIEF   = float(_env("PRICE_DAILY_BRIEF", "15"))

# ── Daily curated brief ──────────────────────────────────────────────────────
BRIEF_HOUR_UTC = int(_env("BRIEF_HOUR_UTC", "5"))   # curator runs at 05:00 UTC
SERVER_SLUG    = "crypto-intel"
NETWORK_BRIEFS = {
    "financial-signals": "$25", "crypto-intel": "$15", "cyber-intel": "$15",
    "patent-intel": "$10", "gov-contracts": "$10", "compliance": "$10",
    "brand-intel": "$5", "weather-intel": "$5", "fact-check": "$5",
    "oss-intel": "$5", "social-intel": "$5", "email-verify": "$5",
    "currency-intel": "$5",
}

# Fleet bearer for free internal sibling calls (bypasses each sibling's x402 gate).
FNET_API_KEY = (_env("FNET_API_KEY") or _env("FORGE_API_KEY") or _env("MINT_API_KEY")).strip()

PUBLIC_MCP_URL = _env("PUBLIC_MCP_URL", "https://crypto-intel-mcp-production.up.railway.app/mcp")

# ── FoundryNet Data Network — full sister-server map ──────────────────────────
_FNET_ALL_SERVERS = {
    "mint-mcp":              "https://mint-mcp-production.up.railway.app/mcp",
    "foundrynet-mcp":        "https://foundrynet-mcp-production.up.railway.app/mcp",
    "gov-contracts-mcp":     "https://gov-contracts-mcp-production.up.railway.app/mcp",
    "brand-intel-mcp":       "https://brand-intel-mcp-production.up.railway.app/mcp",
    "patent-intel-mcp":      "https://patent-intel-mcp-production.up.railway.app/mcp",
    "financial-signals-mcp": "https://financial-signals-mcp-production.up.railway.app/mcp",
    "weather-intel-mcp":     "https://weather-intel-mcp-production.up.railway.app/mcp",
    "cyber-intel-mcp":       "https://cyber-intel-mcp-production.up.railway.app/mcp",
    "compliance-mcp":        "https://compliance-mcp-production.up.railway.app/mcp",
    "academic-intel-mcp":    "https://academic-intel-mcp-production.up.railway.app/mcp",
    "fact-check-mcp":        "https://fact-check-mcp-production.up.railway.app/mcp",
    "oss-intel-mcp":         "https://oss-intel-mcp-production.up.railway.app/mcp",
    "social-intel-mcp":      "https://social-intel-mcp-production.up.railway.app/mcp",
    "crypto-intel-mcp":      "https://crypto-intel-mcp-production.up.railway.app/mcp",
    "market-data-mcp":       "https://market-data-mcp-production.up.railway.app/mcp",
    "email-verify-mcp":      "https://email-verify-mcp-production.up.railway.app/mcp",
    "currency-intel-mcp":    "https://currency-intel-mcp-production.up.railway.app/mcp",
}
SISTER_SERVERS = {k: v for k, v in _FNET_ALL_SERVERS.items() if k != "crypto-intel-mcp"}
