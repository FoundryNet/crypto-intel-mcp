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
import stripe_gate
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


# ── token_risk_scan (PAID) — DeFi Risk Scanner ───────────────────────────────
async def do_token_risk_scan(coin, *, agent_key, payment_tx=None, api_key=None) -> dict:
    """Combine the free price + market_overview reads into a 0-100 token risk
    score (market-cap, liquidity, volatility, sentiment) with flags + a verdict."""
    coin = (coin or "").strip()
    if not coin:
        return {"error": "bad_request", "detail": "coin (id like 'bitcoin') or symbol (like 'BTC') is required"}
    decision = await payment_gate.precheck("token_risk_scan", {"coin": coin},
                                           config.PRICE_TOKEN_RISK, agent_key, payment_tx, api_key)
    if decision["gate"] == "blocked":
        return decision["body"]

    price_data = await do_price(coin)
    if not isinstance(price_data, dict) or price_data.get("price_usd") is None or "error" in price_data:
        return {"error": f"Token {coin} not found", "risk_score": None,
                "billing": _billing(decision)}

    price_usd = price_data.get("price_usd")
    market_cap = price_data.get("market_cap")
    volume_24h = price_data.get("volume_24h")
    change_24h = price_data.get("change_24h_pct")

    overview = await do_market_overview()
    fg = (overview or {}).get("fear_greed_index") if isinstance(overview, dict) else None
    fg_value = fg.get("value") if isinstance(fg, dict) else None

    risk_score = 0
    risk_flags: list = []

    # Market-cap risk (smaller cap → riskier).
    if market_cap is not None:
        if market_cap < 1_000_000:
            risk_score += 35
            risk_flags.append("micro_cap_under_1M")
        elif market_cap < 10_000_000:
            risk_score += 20
            risk_flags.append("small_cap_under_10M")
        elif market_cap < 100_000_000:
            risk_score += 10
            risk_flags.append("mid_cap_under_100M")

    # Liquidity risk (24h volume vs market cap).
    if market_cap and volume_24h is not None and market_cap > 0:
        liquidity = volume_24h / market_cap
        if liquidity < 0.01:
            risk_score += 25
            risk_flags.append("very_low_liquidity")
        elif liquidity < 0.05:
            risk_score += 10
            risk_flags.append("low_liquidity")

    # Volatility risk (absolute 24h move).
    if change_24h is not None:
        ac = abs(change_24h)
        if ac > 20:
            risk_score += 25
            risk_flags.append(f"extreme_volatility_{round(ac, 1)}")
        elif ac > 10:
            risk_score += 15
            risk_flags.append(f"high_volatility_{round(ac, 1)}")

    # Market sentiment (extreme fear).
    if fg_value is not None:
        try:
            if int(fg_value) < 25:
                risk_score += 10
                risk_flags.append("extreme_fear_market")
        except (TypeError, ValueError):
            pass

    risk_score = min(risk_score, 100)
    if risk_score > 70:
        risk_level = "critical"
        recommendation = "Extreme risk — do not invest without thorough due diligence"
    elif risk_score > 50:
        risk_level = "high"
        recommendation = "High risk — significant volatility or liquidity concerns"
    elif risk_score > 30:
        risk_level = "moderate"
        recommendation = "Moderate risk — standard caution advised"
    else:
        risk_level = "low"
        recommendation = "Lower risk — established token with reasonable metrics"

    out = {
        "token": price_data.get("coin") or coin,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "price_usd": price_usd,
        "market_cap": market_cap,
        "volume_24h": volume_24h,
        "change_24h_pct": change_24h,
        "risk_flags": risk_flags,
        "recommendation": recommendation,
        "billing": _billing(decision),
    }
    out["provenance"] = await asyncio.to_thread(
        mint_integration.attest_data,
        {"token": out["token"], "risk_score": risk_score, "risk_level": risk_level,
         "risk_flags": risk_flags},
        "analysis", "token risk scan")
    return out


# ── daily_brief (premium, curated) ────────────────────────────────────────────
async def do_daily_brief(date, *, agent_key, payment_tx=None, api_key=None,
                         stripe_token=None) -> dict:
    day = (date or _today()).strip()

    # Stripe rail (parallel to x402): a paid Checkout Session unlocks the brief.
    stripe_err = None
    if stripe_token and stripe_gate.is_active():
        sv = await stripe_gate.verify_session(stripe_token, config.PRICE_DAILY_BRIEF,
                                              tool="daily_brief", agent_key=agent_key)
        if sv["ok"]:
            brief = await daily_curator.get_brief(day)
            if not brief:
                return {"error": "not_available",
                        "detail": f"No brief for {day} (not yet generated, or expired at midnight UTC). "
                                  f"Briefs are curated daily at {config.BRIEF_HOUR_UTC:02d}:00 UTC.",
                        "billing": "stripe"}
            await daily_curator.bump_purchase(day)
            return {**brief, "billing": "stripe", "stripe_session": sv["session"]}
        stripe_err = sv.get("detail")  # surface on the 402 below

    decision = await payment_gate.precheck("daily_brief", {"date": day},
                                           config.PRICE_DAILY_BRIEF, agent_key, payment_tx, api_key)
    if decision["gate"] == "blocked":
        return stripe_gate.augment_402(decision["body"], config.PRICE_DAILY_BRIEF,
                                       stripe_error=stripe_err)
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
                import asyncio as _aio, mint_integration as _mint, upsell_engine as _upsell_engine
                _hb = await _aio.to_thread(_mint.network_heartbeat)
                _av, _ct = await _brief_status_cached()
                result["foundrynet_network"] = {**_hb, **_upsell_engine.get_upsell(
                    brief_price=config.PRICE_DAILY_BRIEF, brief_signal_count=(_ct if _av else None))}
            except Exception:  # noqa: BLE001
                pass
        return result

    return _wrapped


for _upsell_fn in ("do_price_history", "do_whale_alerts", "do_defi_overview", "do_anomaly_scan",
                   "do_token_risk_scan"):
    if _upsell_fn in globals():
        globals()[_upsell_fn] = _make_upsell(globals()[_upsell_fn])



# ── brief_summary ($0.50): structured top-5 sample of today's brief (upsell) ──
def _top_signals(brief: dict, n: int = 5) -> list:
    """Flatten a brief's signals into a flat top-N list — structure-agnostic
    (works whether `signals` is a dict-of-categories or a flat list)."""
    sig = (brief or {}).get("signals")
    items: list = []
    if isinstance(sig, dict):
        for cat, val in sig.items():
            if isinstance(val, list):
                for it in val:
                    items.append({"category": cat, **(it if isinstance(it, dict) else {"value": it})})
            elif isinstance(val, dict):
                items.append({"category": cat, **val})
            elif val not in (None, "", 0):
                items.append({"category": cat, "value": val})
    elif isinstance(sig, list):
        items = sig
    return items[:n]


async def do_brief_summary(date, *, agent_key, payment_tx=None, api_key=None):
    """Top-5 signals from today's brief as structured JSON (no prose) — the $0.50
    sample that upsells the full daily_brief."""
    from datetime import datetime, timezone
    day = (date or datetime.now(timezone.utc).strftime("%Y-%m-%d")).strip()
    dec = await payment_gate.precheck("brief_summary", {"date": day}, config.PRICE_BRIEF_SUMMARY,
                                      agent_key, payment_tx, api_key)
    if dec["gate"] == "blocked":
        return dec["body"]
    brief = await daily_curator.get_brief(day)
    if not brief:
        return {"error": "not_available",
                "detail": f"No brief for {day} yet (curated daily; expires next midnight UTC).",
                "billing": _billing(dec)}
    return {
        "date": day,
        "top_signals": _top_signals(brief, 5),
        "total_signals": brief.get("signal_count"),
        "full_brief": {"tool": "daily_brief", "price_usd": config.PRICE_DAILY_BRIEF,
                       "note": "Full brief returns all signals with complete detail + MINT attestation."},
        "billing": _billing(dec),
    }
