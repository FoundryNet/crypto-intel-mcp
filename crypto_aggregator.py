"""Crypto aggregator — refreshes the latest market snapshot every
AGG_INTERVAL_MINUTES and keeps the daily OHLCV history filled in. Runs in-process
(build_dual_app starts the loop), so no external cron is needed.

  • run_aggregation()  — fetch CoinGecko top-100 markets → upsert crypto_prices and
                         append today's OHLCV row; fetch DeFiLlama protocols →
                         upsert defi_protocols (top 100); fetch Fear & Greed →
                         upsert fear_greed. btc_dominance is derived from caps.
  • backfill_ohlcv()   — on first run, populate ~90 days of daily OHLCV for the
                         top ~30 coins so price_history / anomaly_scan work at once.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import config
import crypto_sources as src
import supa

logger = logging.getLogger("cry.agg")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _btc_dominance(prices: list) -> Optional[float]:
    total_cap = sum((p.get("market_cap") or 0) for p in prices)
    btc = next((p for p in prices if p.get("coin_id") == "bitcoin"), None)
    if btc and total_cap:
        return round(100 * (btc.get("market_cap") or 0) / total_cap, 4)
    return None


async def run_aggregation() -> dict:
    """Fetch the latest markets / DeFi / Fear & Greed and persist them."""
    today = _today()

    # 1) Prices (top 100) → snapshot + today's OHLCV row.
    prices = await src.markets(per_page=100, page=1)
    if prices:
        snap = [{"coin_id": p["coin_id"], "symbol": p["symbol"], "name": p["name"],
                 "price_usd": p["price_usd"], "change_24h_pct": p["change_24h_pct"],
                 "volume_24h": p["volume_24h"], "market_cap": p["market_cap"],
                 "last_updated": p.get("last_updated")} for p in prices]
        await supa.upsert_prices(snap)
        ohlcv = [{"coin_id": p["coin_id"], "date": today,
                  "price": p["price_usd"], "volume": p["volume_24h"]}
                 for p in prices if p.get("price_usd") is not None]
        if ohlcv:
            await supa.upsert_ohlcv(ohlcv)
        dom = _btc_dominance(prices)
        logger.info(f"aggregation: {len(prices)} coins, btc_dominance={dom}")
    else:
        logger.warning("aggregation: no prices fetched")

    # 2) DeFi protocols (top 100 by TVL).
    protocols = await src.defi_protocols()
    if protocols:
        protocols.sort(key=lambda x: (x.get("tvl") or 0), reverse=True)
        rows = [{"name": p["name"], "category": p.get("category"),
                 "tvl": p.get("tvl"), "change_1d": p.get("change_1d"),
                 "change_7d": p.get("change_7d"), "chain": p.get("chain")}
                for p in protocols[:100]]
        await supa.upsert_defi_protocols(rows)
        logger.info(f"aggregation: {len(rows)} DeFi protocols")

    # 3) Fear & Greed index.
    fg = await src.fear_greed()
    if fg.get("value") is not None:
        await supa.upsert_fear_greed({"date": fg.get("date") or today,
                                      "value": fg["value"],
                                      "classification": fg.get("classification")})

    return {"ok": bool(prices), "coins": len(prices), "protocols": len(protocols or []),
            "fear_greed": fg.get("value")}


async def backfill_ohlcv() -> int:
    """Populate ~OHLCV_BACKFILL_DAYS of daily OHLCV for the top OHLCV_BACKFILL_COINS
    coins if the history table is sparse. Idempotent (upsert). Returns rows written."""
    existing = await supa.select("crypto_ohlcv",
                                 {"select": "coin_id", "order": "date.desc", "limit": "5"})
    if len(existing) >= 3:
        return 0  # already have history
    prices = await src.markets(per_page=config.OHLCV_BACKFILL_COINS, page=1)
    n = 0
    for p in prices[:config.OHLCV_BACKFILL_COINS]:
        cid = p["coin_id"]
        try:
            series = await src.market_chart(cid, days=config.OHLCV_BACKFILL_DAYS)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"backfill {cid} error: {e}")
            continue
        rows = [{"coin_id": cid, "date": d["date"], "price": d["price"],
                 "volume": d.get("volume")} for d in series if d.get("price") is not None]
        if rows:
            res = await supa.upsert_ohlcv(rows)
            if not (isinstance(res, dict) and res.get("error")):
                n += len(rows)
        # Gentle pacing — CoinGecko's keyless tier is rate-limited.
        await asyncio.sleep(2)
    logger.info(f"backfill_ohlcv: wrote {n} OHLCV rows across {len(prices)} coins")
    return n


async def agg_loop() -> None:
    """Refresh now (and backfill on cold start), then every AGG_INTERVAL_MINUTES."""
    interval = max(5, config.AGG_INTERVAL_MINUTES) * 60
    # Cold-start: pull markets immediately + backfill OHLCV so tools work at once.
    try:
        if supa.configured():
            await run_aggregation()
            await backfill_ohlcv()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"initial aggregation error: {e}")
    while True:
        try:
            await asyncio.sleep(interval)
            if supa.configured():
                await run_aggregation()
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.warning(f"agg loop error: {e}")
            await asyncio.sleep(300)
