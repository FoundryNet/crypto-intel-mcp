"""Crypto market sources — all keyless and free. CoinGecko is primary (prices,
markets, OHLCV history, simple price), CoinCap is the latest-price backup,
DeFiLlama serves protocol/chain TVL, and alternative.me serves the Fear & Greed
index. All rates are in USD. Every function returns plain data and never raises.

NOTE: http_util.request_json does NOT follow redirects — use final URLs only.
An optional COINGECKO_API_KEY is sent as the x-cg-demo-api-key header when set.
"""
from __future__ import annotations

import logging

import config
from http_util import request_json

logger = logging.getLogger("cry.sources")

COINGECKO   = "https://api.coingecko.com/api/v3"
COINCAP     = "https://api.coincap.io/v2/assets"
DEFILLAMA   = "https://api.llama.fi"
FEAR_GREED  = "https://api.alternative.me/fng/?limit=1"


def _cg_headers() -> dict | None:
    """CoinGecko demo-key header, when COINGECKO_API_KEY is configured."""
    if config.COINGECKO_API_KEY:
        return {"x-cg-demo-api-key": config.COINGECKO_API_KEY}
    return None


# ── CoinGecko: top markets snapshot ───────────────────────────────────────────
async def markets(per_page: int = 100, page: int = 1) -> list:
    """Top coins by market cap. Returns a list of dicts with id/symbol/name/price/
    24h change/volume/market_cap/last_updated. Empty list on any failure."""
    r = await request_json("GET", f"{COINGECKO}/coins/markets",
                           params={"vs_currency": "usd", "order": "market_cap_desc",
                                   "per_page": per_page, "page": page},
                           headers=_cg_headers(), timeout=config.REQUEST_TIMEOUT)
    if not isinstance(r, list):
        logger.warning(f"markets fetch failed: {r}")
        return []
    out = []
    for c in r:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        out.append({
            "coin_id": c.get("id"),
            "symbol": (c.get("symbol") or "").upper(),
            "name": c.get("name"),
            "price_usd": c.get("current_price"),
            "change_24h_pct": c.get("price_change_percentage_24h"),
            "volume_24h": c.get("total_volume"),
            "market_cap": c.get("market_cap"),
            "last_updated": c.get("last_updated"),
        })
    return out


# ── CoinGecko: OHLCV-ish daily history ────────────────────────────────────────
async def market_chart(coin_id: str, days: int = 30) -> list:
    """Daily (date, price, volume) series for a coin over the last N days. Built
    from CoinGecko market_chart prices[]/total_volumes[] (ms-timestamp arrays).
    Returns an ascending list of {"date","price","volume"}; empty on failure."""
    r = await request_json("GET", f"{COINGECKO}/coins/{coin_id}/market_chart",
                           params={"vs_currency": "usd", "days": days},
                           headers=_cg_headers(), timeout=config.REQUEST_TIMEOUT)
    if not isinstance(r, dict):
        return []
    prices = r.get("prices") or []
    volumes = r.get("total_volumes") or []
    vol_by_day: dict = {}
    for v in volumes:
        if isinstance(v, list) and len(v) >= 2 and v[0] is not None:
            vol_by_day[_day(v[0])] = v[1]
    # One row per day (last observation of the day wins).
    rows: dict = {}
    for p in prices:
        if isinstance(p, list) and len(p) >= 2 and p[0] is not None:
            d = _day(p[0])
            rows[d] = {"date": d, "price": p[1], "volume": vol_by_day.get(d)}
    return [rows[d] for d in sorted(rows)]


def _day(ms) -> str:
    """ms epoch → YYYY-MM-DD (UTC)."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


# ── CoinGecko: simple price (live fallback for `price`) ────────────────────────
async def simple_price(coin_ids: list) -> dict:
    """Live USD price for one or more coin ids. Returns CoinGecko's raw shape
    {coin_id: {usd, usd_24h_change, usd_market_cap, usd_24h_vol}}. Empty on fail."""
    if not coin_ids:
        return {}
    r = await request_json("GET", f"{COINGECKO}/simple/price",
                           params={"ids": ",".join(coin_ids), "vs_currencies": "usd",
                                   "include_24hr_change": "true",
                                   "include_market_cap": "true",
                                   "include_24hr_vol": "true"},
                           headers=_cg_headers(), timeout=config.REQUEST_TIMEOUT)
    return r if isinstance(r, dict) and "error" not in r else {}


# ── CoinCap: latest-price backup ──────────────────────────────────────────────
async def coincap_price(symbol_or_id: str) -> dict:
    """Backup latest price from CoinCap. Matches on id first, then symbol. Returns
    {"price_usd","change_24h_pct","volume_24h","market_cap"} or {}."""
    r = await request_json("GET", COINCAP, params={"limit": 2000},
                           timeout=config.REQUEST_TIMEOUT)
    if not isinstance(r, dict):
        return {}
    want = (symbol_or_id or "").lower()
    for a in (r.get("data") or []):
        if not isinstance(a, dict):
            continue
        if a.get("id", "").lower() == want or a.get("symbol", "").lower() == want:
            return {
                "price_usd": _f(a.get("priceUsd")),
                "change_24h_pct": _f(a.get("changePercent24Hr")),
                "volume_24h": _f(a.get("volumeUsd24Hr")),
                "market_cap": _f(a.get("marketCapUsd")),
            }
    return {}


def _f(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


# ── DeFiLlama: protocols + chains TVL ─────────────────────────────────────────
async def defi_protocols() -> list:
    """All DeFi protocols with name/category/tvl/change_1d/change_7d/chain.
    Returns a list (empty on failure)."""
    r = await request_json("GET", f"{DEFILLAMA}/protocols", timeout=config.REQUEST_TIMEOUT)
    if not isinstance(r, list):
        logger.warning(f"defi protocols fetch failed: {r}")
        return []
    out = []
    for p in r:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        out.append({
            "name": p.get("name"),
            "category": p.get("category"),
            "tvl": p.get("tvl"),
            "change_1d": p.get("change_1d"),
            "change_7d": p.get("change_7d"),
            "chain": p.get("chain") or (p.get("chains") or [None])[0],
        })
    return out


async def defi_chains() -> list:
    """Per-chain TVL totals. Returns a list of {name, tvl} (empty on failure)."""
    r = await request_json("GET", f"{DEFILLAMA}/v2/chains", timeout=config.REQUEST_TIMEOUT)
    if not isinstance(r, list):
        return []
    return [{"name": c.get("name"), "tvl": c.get("tvl")}
            for c in r if isinstance(c, dict) and c.get("name")]


# ── alternative.me: Fear & Greed index ────────────────────────────────────────
async def fear_greed() -> dict:
    """Latest crypto Fear & Greed index. Returns {"value": int, "classification":
    str, "date": YYYY-MM-DD} or {} on failure."""
    r = await request_json("GET", FEAR_GREED, timeout=config.REQUEST_TIMEOUT)
    if not isinstance(r, dict):
        return {}
    data = r.get("data") or []
    if not data or not isinstance(data[0], dict):
        return {}
    d0 = data[0]
    try:
        value = int(d0.get("value"))
    except (TypeError, ValueError):
        return {}
    from datetime import datetime, timezone
    date = None
    ts = d0.get("timestamp")
    if ts:
        try:
            date = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError):
            date = None
    return {"value": value, "classification": d0.get("value_classification"),
            "date": date}
