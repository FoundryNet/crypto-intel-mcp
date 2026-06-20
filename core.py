"""Shared logic behind the MCP tools and REST routes.

price/market_overview are FREE and served from the latest snapshot (live fallback on
a cold cache). price_history/whale_alerts/defi_overview/anomaly_scan are PAID (x402).
All values are in USD. Symbols (e.g. "BTC") are resolved to CoinGecko ids (e.g.
"bitcoin") via the latest snapshot, with a live /simple/price fallback.
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import time as _time
from datetime import datetime, timedelta, timezone

import config
import crypto_sources as src
import daily_curator
import mint_integration
import payment_gate
import supa

logger = logging.getLogger("cry.core")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _billing(decision: dict) -> dict:
    g = decision.get("gate")
    if g == "free":
        cap, cnt = decision.get("cap"), decision.get("count")
        return {"tier": "free", "used_today": cnt, "daily_free": cap,
                "remaining_today": (cap - cnt) if (cap is not None and cnt is not None) else None}
    if g == "paid":
        return {"tier": "paid", "charged_usdc": decision.get("amount_usdc")}
    if g == "api_key":
        return {"tier": "api_key", "note": "billed to your Forge account"}
    return {"tier": "free", "note": "gating inert"}


# ── coin id/symbol resolution (cached from the latest snapshot) ───────────────
_coin_cache = {"ts": 0.0, "by_symbol": {}, "by_id": set()}


async def _coin_index() -> tuple[dict, set]:
    """Return ({SYMBOL: coin_id}, {coin_id, …}) from the latest snapshot, cached 5 min.
    Most-capitalized coin wins a symbol collision (rows arrive cap-desc)."""
    now = _time.time()
    c = _coin_cache
    if c["by_id"] and (now - c["ts"]) < 300:
        return c["by_symbol"], c["by_id"]
    rows = await supa.get_all_prices()
    by_symbol, by_id = {}, set()
    for r in rows:
        cid, sym = r.get("coin_id"), (r.get("symbol") or "").upper()
        if cid:
            by_id.add(cid)
        if sym and sym not in by_symbol and cid:
            by_symbol[sym] = cid
    if by_id:
        c.update(ts=now, by_symbol=by_symbol, by_id=by_id)
    return by_symbol, by_id


async def _resolve_coin_id(coin: str) -> str | None:
    """Map a coin id ("bitcoin") or symbol ("BTC") to a CoinGecko id. Returns the
    input unchanged when it already looks like a known id; None if unresolvable."""
    if not coin:
        return None
    raw = coin.strip()
    by_symbol, by_id = await _coin_index()
    if raw.lower() in by_id:
        return raw.lower()
    if raw.upper() in by_symbol:
        return by_symbol[raw.upper()]
    # Unknown to the cache — assume it's a raw CoinGecko id (live fallback verifies).
    return raw.lower()


# ── price (FREE) ──────────────────────────────────────────────────────────────
async def do_price(coin: str) -> dict:
    coin = (coin or "").strip()
    if not coin:
        return {"error": "bad_request", "detail": "coin (id like 'bitcoin') or symbol (like 'BTC') is required"}
    coin_id = await _resolve_coin_id(coin)

    # 1) Prefer the cached snapshot (by id, then by raw symbol).
    row = await supa.get_price_by_id(coin_id) if coin_id else None
    if not row:
        row = await supa.get_price_by_symbol(coin)
    if row and row.get("price_usd") is not None:
        return {"coin": row.get("coin_id"), "symbol": row.get("symbol"),
                "price_usd": row.get("price_usd"), "change_24h_pct": row.get("change_24h_pct"),
                "volume_24h": row.get("volume_24h"), "market_cap": row.get("market_cap"),
                "last_updated": row.get("last_updated"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "note": "FoundryNet Data Network — free crypto price gateway",
                "billing": {"tier": "free"}}

    # 2) Live fallback: CoinGecko /simple/price (cache the snapshot on the way back).
    if coin_id:
        sp = await src.simple_price([coin_id])
        d = sp.get(coin_id) if isinstance(sp, dict) else None
        if d:
            out = {"coin": coin_id, "symbol": coin.upper() if coin.upper() != coin.lower() else None,
                   "price_usd": d.get("usd"), "change_24h_pct": d.get("usd_24h_change"),
                   "volume_24h": d.get("usd_24h_vol"), "market_cap": d.get("usd_market_cap"),
                   "last_updated": None, "timestamp": datetime.now(timezone.utc).isoformat(),
                   "note": "FoundryNet Data Network — free crypto price gateway",
                   "billing": {"tier": "free"}}
            try:
                await supa.upsert_prices([{"coin_id": coin_id, "symbol": out["symbol"] or "",
                                           "name": None, "price_usd": out["price_usd"],
                                           "change_24h_pct": out["change_24h_pct"],
                                           "volume_24h": out["volume_24h"],
                                           "market_cap": out["market_cap"]}])
            except Exception:  # noqa: BLE001
                pass
            return out

    # 3) CoinCap backup.
    cc = await src.coincap_price(coin)
    if cc.get("price_usd") is not None:
        return {"coin": coin_id or coin.lower(), "symbol": coin.upper() if coin.upper() != coin.lower() else None,
                **cc, "last_updated": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "note": "FoundryNet Data Network — free crypto price gateway (CoinCap backup)",
                "billing": {"tier": "free"}}

    return {"error": "not_found", "detail": f"unknown coin '{coin}' (use a CoinGecko id like 'bitcoin' or a symbol like 'BTC')"}


# ── market_overview (FREE) ────────────────────────────────────────────────────
async def do_market_overview() -> dict:
    prices = await supa.get_all_prices()
    if not prices:
        # Cold cache — pull a live snapshot so the free tool still answers.
        live = await src.markets(per_page=100, page=1)
        if live:
            try:
                await supa.upsert_prices([{
                    "coin_id": p["coin_id"], "symbol": p["symbol"], "name": p["name"],
                    "price_usd": p["price_usd"], "change_24h_pct": p["change_24h_pct"],
                    "volume_24h": p["volume_24h"], "market_cap": p["market_cap"],
                    "last_updated": p.get("last_updated")} for p in live])
            except Exception:  # noqa: BLE001
                pass
            prices = live

    top = [{"symbol": p.get("symbol"), "name": p.get("name"),
            "price_usd": p.get("price_usd"), "change_24h_pct": p.get("change_24h_pct")}
           for p in prices[:20]]
    total_cap = sum((p.get("market_cap") or 0) for p in prices) or None
    btc = next((p for p in prices if p.get("coin_id") == "bitcoin"), None)
    btc_dom = (round(100 * (btc.get("market_cap") or 0) / total_cap, 4)
               if btc and total_cap else None)

    fg = await supa.get_latest_fear_greed()
    if not fg:
        live_fg = await src.fear_greed()
        if live_fg.get("value") is not None:
            fg = live_fg
    fear_greed = ({"value": fg.get("value"), "classification": fg.get("classification")}
                  if fg else None)

    return {"top_coins": top, "total_market_cap_usd": total_cap,
            "btc_dominance_pct": btc_dom, "fear_greed_index": fear_greed,
            "coin_count": len(prices),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "note": "FoundryNet Data Network — free crypto market overview",
            "billing": {"tier": "free"}}


# ── price_history (PAID) ──────────────────────────────────────────────────────
async def do_price_history(coin: str, days, *, agent_key, payment_tx=None, api_key=None) -> dict:
    coin = (coin or "").strip()
    if not coin:
        return {"error": "bad_request", "detail": "coin (id or symbol) is required"}
    try:
        n = max(1, min(int(days or 30), 365))
    except (TypeError, ValueError):
        n = 30
    decision = await payment_gate.precheck("price_history", {"coin": coin, "days": n},
                                           config.PRICE_PRICE_HISTORY, agent_key, payment_tx, api_key)
    if decision["gate"] == "blocked":
        return decision["body"]
    coin_id = await _resolve_coin_id(coin)
    if not coin_id:
        return {"error": "not_found", "detail": f"unknown coin '{coin}'", "billing": _billing(decision)}
    series = await src.market_chart(coin_id, days=n)
    if series:
        # Cache into crypto_ohlcv so anomaly_scan / brief benefit too.
        rows = [{"coin_id": coin_id, "date": d["date"], "price": d["price"],
                 "volume": d.get("volume")} for d in series if d.get("price") is not None]
        if rows:
            try:
                await supa.upsert_ohlcv(rows)
            except Exception:  # noqa: BLE001
                pass
    if not series:
        return {"error": "not_found", "detail": f"no history for {coin_id}",
                "billing": _billing(decision)}
    return {"coin": coin_id, "days": n, "observations": len(series),
            "history": [{"date": d["date"], "price": d["price"], "volume": d.get("volume")}
                        for d in series],
            "billing": _billing(decision)}


# ── whale_alerts (PAID) ───────────────────────────────────────────────────────
async def do_whale_alerts(coin, min_value_usd, *, agent_key, payment_tx=None, api_key=None) -> dict:
    decision = await payment_gate.precheck("whale_alerts",
                                           {"coin": coin or "", "min_value_usd": min_value_usd},
                                           config.PRICE_WHALE_ALERTS, agent_key, payment_tx, api_key)
    if decision["gate"] == "blocked":
        return decision["body"]
    prices = await supa.get_all_prices()
    if not prices:
        prices = await src.markets(per_page=100, page=1)
    try:
        floor = float(min_value_usd) if min_value_usd is not None else 0.0
    except (TypeError, ValueError):
        floor = 0.0

    target_id = await _resolve_coin_id(coin) if coin else None
    alerts = []
    for p in prices:
        if target_id and p.get("coin_id") != target_id:
            continue
        cap, vol = p.get("market_cap"), p.get("volume_24h")
        if not vol or vol < floor:
            continue
        ratio = round(100 * vol / cap, 2) if cap else None
        # Flag coins whose 24h volume is unusually large vs market cap, or absolutely large.
        signal = None
        if ratio is not None and ratio >= 100:
            signal = "very_high_turnover"
        elif ratio is not None and ratio >= 40:
            signal = "elevated_turnover"
        elif vol >= 1_000_000_000:
            signal = "large_absolute_volume"
        if signal:
            alerts.append({"symbol": p.get("symbol"), "coin": p.get("coin_id"),
                           "volume_24h": vol, "volume_vs_market_cap_pct": ratio,
                           "change_24h_pct": p.get("change_24h_pct"), "signal": signal})
    alerts.sort(key=lambda a: (a.get("volume_vs_market_cap_pct") or 0,
                               a.get("volume_24h") or 0), reverse=True)
    return {"method": "volume_derived",
            "disclaimer": ("Notable flows are derived from 24h trading volume and "
                           "volume-vs-market-cap turnover, NOT raw on-chain transfers "
                           "(which require a paid whale-tracking API)."),
            "coin": target_id, "min_value_usd": floor, "count": len(alerts[:25]),
            "alerts": alerts[:25], "billing": _billing(decision)}


# ── defi_overview (PAID) ──────────────────────────────────────────────────────
async def do_defi_overview(protocol, *, agent_key, payment_tx=None, api_key=None) -> dict:
    decision = await payment_gate.precheck("defi_overview", {"protocol": protocol or ""},
                                           config.PRICE_DEFI_OVERVIEW, agent_key, payment_tx, api_key)
    if decision["gate"] == "blocked":
        return decision["body"]

    if protocol and str(protocol).strip():
        name = str(protocol).strip()
        row = await supa.get_defi_protocol(name)
        if not row:
            # Live fallback: find by case-insensitive name in the full list.
            live = await src.defi_protocols()
            row = next((p for p in live if (p.get("name") or "").lower() == name.lower()), None)
        if not row:
            return {"error": "not_found", "detail": f"unknown DeFi protocol '{name}'",
                    "billing": _billing(decision)}
        return {"protocol": row.get("name"), "category": row.get("category"),
                "tvl": row.get("tvl"), "change_1d": row.get("change_1d"),
                "change_7d": row.get("change_7d"), "chain": row.get("chain"),
                "billing": _billing(decision)}

    protocols = await supa.get_defi_protocols(limit=100)
    if not protocols:
        protocols = await src.defi_protocols()
    total_tvl = sum((p.get("tvl") or 0) for p in protocols) or None
    by_category: dict = {}
    for p in protocols:
        cat = p.get("category") or "Other"
        by_category[cat] = by_category.get(cat, 0) + (p.get("tvl") or 0)
    categories = sorted(({"category": c, "tvl": round(t, 2)} for c, t in by_category.items()),
                        key=lambda x: x["tvl"], reverse=True)[:15]
    top = sorted(protocols, key=lambda p: (p.get("tvl") or 0), reverse=True)[:15]
    top_protocols = [{"name": p.get("name"), "category": p.get("category"),
                      "tvl": p.get("tvl"), "change_1d": p.get("change_1d"),
                      "change_7d": p.get("change_7d"), "chain": p.get("chain")}
                     for p in top]
    return {"total_defi_tvl_usd": total_tvl, "protocol_count": len(protocols),
            "top_protocols": top_protocols, "by_category": categories,
            "billing": _billing(decision)}


# ── anomaly_scan (PAID) ───────────────────────────────────────────────────────
async def do_anomaly_scan(coin, *, agent_key, payment_tx=None, api_key=None) -> dict:
    decision = await payment_gate.precheck("anomaly_scan", {"coin": coin or ""},
                                           config.PRICE_ANOMALY_SCAN, agent_key, payment_tx, api_key)
    if decision["gate"] == "blocked":
        return decision["body"]

    target_id = await _resolve_coin_id(coin) if coin else None
    prices = await supa.get_all_prices()
    if not prices:
        prices = await src.markets(per_page=100, page=1)
    universe = [p for p in prices if (not target_id or p.get("coin_id") == target_id)]

    anomalies = []
    from_date = (datetime.now(timezone.utc).date() - timedelta(days=31)).strftime("%Y-%m-%d")
    for p in universe:
        cid = p.get("coin_id")
        sym = p.get("symbol")
        cap, vol = p.get("market_cap"), p.get("volume_24h")
        chg = p.get("change_24h_pct")

        # 1) Unusual volume vs market cap.
        if cap and vol and cap > 0:
            ratio = 100 * vol / cap
            if ratio >= 80:
                anomalies.append({"symbol": sym, "coin": cid, "type": "volume_spike",
                                  "severity": "high" if ratio >= 150 else "medium",
                                  "detail": f"24h volume is {round(ratio, 1)}% of market cap"})

        # 2) Large 24h move.
        if chg is not None and abs(chg) >= 15:
            anomalies.append({"symbol": sym, "coin": cid, "type": "large_24h_move",
                              "severity": "high" if abs(chg) >= 30 else "medium",
                              "detail": f"{round(chg, 1)}% in 24h"})

        # 3) Price divergence from the 7d / 30d moving average (from OHLCV history).
        hist = await supa.get_ohlcv(cid, from_date) if cid else []
        closes = [h["price"] for h in hist if h.get("price") is not None]
        cur = p.get("price_usd")
        if cur and len(closes) >= 7:
            ma7 = statistics.fmean(closes[-7:])
            ma30 = statistics.fmean(closes[-30:]) if len(closes) >= 30 else None
            if ma7 and abs(cur - ma7) / ma7 >= 0.20:
                anomalies.append({"symbol": sym, "coin": cid, "type": "ma7_divergence",
                                  "severity": "medium",
                                  "detail": f"price {round(100 * (cur - ma7) / ma7, 1)}% off 7d avg"})
            if ma30 and abs(cur - ma30) / ma30 >= 0.35:
                anomalies.append({"symbol": sym, "coin": cid, "type": "ma30_divergence",
                                  "severity": "high",
                                  "detail": f"price {round(100 * (cur - ma30) / ma30, 1)}% off 30d avg"})

    _rank = {"high": 2, "medium": 1, "low": 0}
    anomalies.sort(key=lambda a: _rank.get(a.get("severity"), 0), reverse=True)
    anomalies = anomalies[:30]

    result = {"coin": target_id, "scanned": len(universe), "anomaly_count": len(anomalies),
              "anomalies": anomalies, "billing": _billing(decision)}
    result["provenance"] = await asyncio.to_thread(
        mint_integration.attest_data,
        {"coin": target_id, "anomaly_count": len(anomalies),
         "anomalies": [{"symbol": a["symbol"], "type": a["type"]} for a in anomalies]},
        "analysis", "anomaly_scan result")
    return result


# ── daily_brief (premium, curated) ────────────────────────────────────────────
async def do_daily_brief(date, *, agent_key, payment_tx=None, api_key=None) -> dict:
    day = (date or _today()).strip()
    decision = await payment_gate.precheck("daily_brief", {"date": day},
                                           config.PRICE_DAILY_BRIEF, agent_key, payment_tx, api_key)
    if decision["gate"] == "blocked":
        return decision["body"]
    brief = await daily_curator.get_brief(day)
    if not brief:
        return {"error": "not_available",
                "detail": f"No brief for {day} (not yet generated, or expired at midnight UTC). "
                          f"Briefs are curated daily at {config.BRIEF_HOUR_UTC:02d}:00 UTC.",
                "billing": _billing(decision)}
    await daily_curator.bump_purchase(day)
    return {**brief, "billing": _billing(decision)}


def mint_info() -> dict:
    return {
        "network": "FoundryNet Data Network", **mint_integration.network_feed_block(),
        "message": ("Attest your agent's crypto market reads and anomaly analysis with "
                    "MINT Protocol for verifiable on-chain proof of work."),
        "positioning": ("A free price + market-overview gateway (price/market_overview) "
                        "plus paid history, DeFi TVL, volume-derived whale flows and "
                        "anomaly scans — keyless market data for trading agents."),
        "mint_protocol": {"mcp_endpoint": "https://mint-mcp-production.up.railway.app/mcp",
                          "info_url": "https://mint.foundrynet.io",
                          "tools": ["mint_register", "mint_attest", "mint_verify",
                                    "mint_rate", "mint_recommend", "mint_discover"]},
        "see_also": config.SISTER_SERVERS,
    }


# ── Soft upsell: surface the daily_brief on every paid, non-brief response ─────
import time as _upsell_time

_brief_upsell_cache = {"day": None, "ts": 0.0, "available": False, "count": 0}


async def _brief_status_cached() -> tuple[bool, int]:
    day = _upsell_time.strftime("%Y-%m-%d", _upsell_time.gmtime())
    now = _upsell_time.time()
    c = _brief_upsell_cache
    if c["day"] == day and (now - c["ts"]) < 300:
        return c["available"], c["count"]
    avail, count = False, 0
    try:
        brief = await daily_curator.get_brief(day)
        if brief:
            avail, count = True, int(brief.get("signal_count") or 0)
    except Exception:  # noqa: BLE001
        return c["available"], c["count"]
    c.update(day=day, ts=now, available=avail, count=count)
    return avail, count


async def _available_intelligence() -> dict:
    avail, count = await _brief_status_cached()
    return {"daily_brief": {
        "available": avail,
        "signal_count": count,
        "price_usd": config.PRICE_DAILY_BRIEF,
        "tool": "daily_brief",
        "note": "Curated daily intelligence — more efficient than individual queries",
    }}


def _make_upsell(_fn):
    import functools

    @functools.wraps(_fn)
    async def _wrapped(*a, **k):
        result = await _fn(*a, **k)
        if isinstance(result, dict) and "error" not in result and "payment_required" not in result:
            try:
                result["available_intelligence"] = await _available_intelligence()
            except Exception:  # noqa: BLE001
                pass
            try:
                import asyncio as _aio, mint_integration as _mint
                result["foundrynet_network"] = await _aio.to_thread(_mint.network_heartbeat)
            except Exception:  # noqa: BLE001
                pass
        return result

    return _wrapped


for _upsell_fn in ("do_price_history", "do_whale_alerts", "do_defi_overview", "do_anomaly_scan"):
    if _upsell_fn in globals():
        globals()[_upsell_fn] = _make_upsell(globals()[_upsell_fn])
