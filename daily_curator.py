"""Daily curated brief — crypto-intel.

Runs once a day at BRIEF_HOUR_UTC (05:00 UTC). It computes the biggest 24h gainers
and losers, the current Fear & Greed reading, total market cap + BTC dominance, the
top DeFi TVL movers, and any flagged anomalies, then attests the package through
MINT and upserts it into `daily_briefs`. The paid daily_brief tool reads it back.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import config
import mint_integration
import supa

logger = logging.getLogger("cry.curator")

SERVER = config.SERVER_SLUG
PRICE = config.PRICE_DAILY_BRIEF


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _expires_at(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return (d + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")


def related_briefs(exclude: str) -> list:
    return [{"server": s, "price": p, "tool": "daily_brief"}
            for s, p in config.NETWORK_BRIEFS.items() if s != exclude]


def _btc_dominance(prices: list):
    total_cap = sum((p.get("market_cap") or 0) for p in prices)
    btc = next((p for p in prices if p.get("coin_id") == "bitcoin"), None)
    if btc and total_cap:
        return round(100 * (btc.get("market_cap") or 0) / total_cap, 4)
    return None


async def _curate_signals() -> tuple[dict, int]:
    prices = await supa.get_all_prices()
    movers = [p for p in prices if p.get("change_24h_pct") is not None]
    movers.sort(key=lambda p: p["change_24h_pct"], reverse=True)
    gainers = [{"symbol": p.get("symbol"), "name": p.get("name"),
                "price_usd": p.get("price_usd"), "change_24h_pct": p.get("change_24h_pct")}
               for p in movers[:5]]
    losers = [{"symbol": p.get("symbol"), "name": p.get("name"),
               "price_usd": p.get("price_usd"), "change_24h_pct": p.get("change_24h_pct")}
              for p in movers[-5:]][::-1]

    total_market_cap = sum((p.get("market_cap") or 0) for p in prices) or None
    btc_dominance = _btc_dominance(prices)

    fg = await supa.get_latest_fear_greed()
    fear_greed = ({"value": fg.get("value"), "classification": fg.get("classification")}
                  if fg else None)

    # Top DeFi TVL movers (by 1d change).
    protocols = await supa.get_defi_protocols(limit=100)
    defi_movers = [p for p in protocols if p.get("change_1d") is not None]
    defi_movers.sort(key=lambda p: abs(p["change_1d"]), reverse=True)
    top_defi_movers = [{"name": p.get("name"), "category": p.get("category"),
                        "tvl": p.get("tvl"), "change_1d": p.get("change_1d")}
                       for p in defi_movers[:5]]

    # Anomalies — coins with the largest 24h volume relative to market cap.
    anomalies = []
    for p in prices:
        cap, vol = p.get("market_cap"), p.get("volume_24h")
        if cap and vol and cap > 0:
            ratio = round(100 * vol / cap, 2)
            if ratio >= 100:  # 24h volume >= market cap is unusual
                anomalies.append({"symbol": p.get("symbol"),
                                  "volume_vs_market_cap_pct": ratio,
                                  "change_24h_pct": p.get("change_24h_pct")})
    anomalies.sort(key=lambda a: a["volume_vs_market_cap_pct"], reverse=True)
    anomalies = anomalies[:5]

    signals = {
        "top_gainers_24h": gainers,
        "top_losers_24h": losers,
        "fear_greed_index": fear_greed,
        "total_market_cap_usd": total_market_cap,
        "btc_dominance_pct": btc_dominance,
        "top_defi_tvl_movers": top_defi_movers,
        "anomalies": anomalies,
    }
    count = (len(gainers) + len(losers) + len(top_defi_movers) + len(anomalies)
             + (1 if fear_greed else 0))
    return signals, count


async def run_curation(date_str: str | None = None) -> dict:
    date_str = date_str or _today()
    signals, count = await _curate_signals()

    brief = {
        "brief_date": date_str, "server": SERVER, "signal_count": count,
        "signals": signals, "expires_at": _expires_at(date_str),
        "related_briefs": related_briefs(SERVER),
    }
    attestation = await asyncio.to_thread(
        mint_integration.attest_data, brief, "analysis",
        f"Daily {SERVER} brief: {count} crypto signals")
    brief["provenance"] = attestation

    row = {
        "brief_date": date_str, "brief_data": brief, "signal_count": count,
        "attestation_hash": attestation.get("attestation_hash"),
        "expires_at": _expires_at(date_str),
    }
    res = await supa.upsert("daily_briefs", [row], "brief_date")
    if isinstance(res, dict) and res.get("error"):
        logger.warning(f"daily brief upsert failed: {str(res)[:200]}")
    else:
        logger.info(f"daily brief stored: {date_str} ({count} crypto signals)")
    return brief


async def get_brief(date_str: str | None = None) -> dict | None:
    date_str = date_str or _today()
    rows = await supa.select("daily_briefs",
                             {"select": "*", "brief_date": f"eq.{date_str}", "limit": "1"})
    if not rows:
        return None
    row = rows[0]
    exp = row.get("expires_at")
    if exp:
        try:
            if datetime.now(timezone.utc) >= datetime.fromisoformat(exp.replace("Z", "+00:00")):
                return None
        except Exception:  # noqa: BLE001
            pass
    return row.get("brief_data")


async def bump_purchase(date_str: str) -> None:
    try:
        await supa.rpc("increment_brief_purchase", {"p_brief_date": date_str})
    except Exception:  # noqa: BLE001
        pass


async def curator_loop() -> None:
    while True:
        now = datetime.now(timezone.utc)
        secs = now.hour * 3600 + now.minute * 60 + now.second
        wait = (config.BRIEF_HOUR_UTC * 3600 - secs) % 86400 or 86400
        try:
            await asyncio.sleep(wait)
            if supa.configured():
                await run_curation()
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.warning(f"curator loop error: {e}")
            await asyncio.sleep(3600)
