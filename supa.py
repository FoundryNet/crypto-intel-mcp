"""Supabase PostgREST client for crypto-intel-mcp (standalone crypto project).

Backs the latest-price snapshot (crypto_prices), the daily OHLCV history
(crypto_ohlcv), the DeFi protocol cache (defi_protocols), the Fear & Greed index
(fear_greed), the free-tier counter (crypto_claim_free_query RPC), the x402
payment ledger (crypto_payments), and the daily brief (daily_briefs). All values
are in USD. Every helper returns plain data and never raises.
"""
from __future__ import annotations

import logging
from typing import Optional

import config
from http_util import request_json

logger = logging.getLogger("cry.supa")


def configured() -> bool:
    return bool(config.SUPABASE_URL and config.SUPABASE_SERVICE_KEY)


def _headers(extra: Optional[dict] = None) -> dict:
    h = {"apikey": config.SUPABASE_SERVICE_KEY,
         "Authorization": f"Bearer {config.SUPABASE_SERVICE_KEY}",
         "Content-Type": "application/json", "Accept": "application/json"}
    if extra:
        h.update(extra)
    return h


def _url(path: str) -> str:
    return f"{config.SUPABASE_URL}/rest/v1/{path}"


async def _select(table: str, params: dict) -> list:
    if not configured():
        return []
    r = await request_json("GET", _url(table), headers=_headers(),
                           params=params, timeout=config.REQUEST_TIMEOUT)
    if isinstance(r, list):
        return r
    logger.warning(f"supa select {table} failed: {r}")
    return []


async def _rpc(fn: str, body: dict):
    if not configured():
        return None
    return await request_json("POST", _url(f"rpc/{fn}"), headers=_headers(),
                              body=body, timeout=config.REQUEST_TIMEOUT)


# ── crypto_prices (latest snapshot) ───────────────────────────────────────────
async def get_all_prices() -> list:
    """All latest coin snapshots, ordered by market cap descending."""
    return await _select("crypto_prices",
                         {"select": "*", "order": "market_cap.desc.nullslast",
                          "limit": "1000"})


async def get_price_by_id(coin_id: str) -> Optional[dict]:
    rows = await _select("crypto_prices",
                         {"coin_id": f"eq.{coin_id}", "select": "*", "limit": "1"})
    return rows[0] if rows else None


async def get_price_by_symbol(symbol: str) -> Optional[dict]:
    rows = await _select("crypto_prices",
                         {"symbol": f"eq.{symbol.upper()}", "select": "*",
                          "order": "market_cap.desc.nullslast", "limit": "1"})
    return rows[0] if rows else None


async def upsert_prices(rows: list) -> dict:
    return await upsert("crypto_prices", rows, "coin_id")


# ── crypto_ohlcv (daily history) ──────────────────────────────────────────────
async def get_ohlcv(coin_id: str, from_date: Optional[str] = None) -> list:
    """Ascending daily (date, price, volume) for a coin since from_date."""
    params = {"coin_id": f"eq.{coin_id}", "select": "date,price,volume",
              "order": "date.asc", "limit": "2000"}
    if from_date:
        params["date"] = f"gte.{from_date}"
    return await _select("crypto_ohlcv", params)


async def upsert_ohlcv(rows: list) -> dict:
    return await upsert("crypto_ohlcv", rows, "coin_id,date")


# ── defi_protocols ────────────────────────────────────────────────────────────
async def get_defi_protocols(limit: int = 100) -> list:
    return await _select("defi_protocols",
                         {"select": "*", "order": "tvl.desc.nullslast",
                          "limit": str(limit)})


async def get_defi_protocol(name: str) -> Optional[dict]:
    rows = await _select("defi_protocols",
                         {"name": f"eq.{name}", "select": "*", "limit": "1"})
    return rows[0] if rows else None


async def upsert_defi_protocols(rows: list) -> dict:
    return await upsert("defi_protocols", rows, "name")


# ── fear_greed ────────────────────────────────────────────────────────────────
async def get_latest_fear_greed() -> Optional[dict]:
    rows = await _select("fear_greed",
                         {"select": "*", "order": "date.desc", "limit": "1"})
    return rows[0] if rows else None


async def upsert_fear_greed(row: dict) -> dict:
    return await upsert("fear_greed", [row], "date")


# ── generic helpers ───────────────────────────────────────────────────────────
async def select(table: str, params: dict) -> list:
    return await _select(table, params)


async def upsert(table: str, rows: list, on_conflict: str) -> dict:
    if not configured() or not rows:
        return {"error": "not_configured_or_empty"}
    r = await request_json("POST", _url(table),
                           headers=_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
                           params={"on_conflict": on_conflict},
                           body=rows, timeout=config.REQUEST_TIMEOUT)
    if isinstance(r, list):
        return {"data": r}
    if isinstance(r, dict) and "error" not in r:
        return {"data": []}
    return r if isinstance(r, dict) else {"error": "bad_response", "detail": str(r)}


async def rpc(fn: str, body: dict):
    return await _rpc(fn, body)


# ── free-tier counter ─────────────────────────────────────────────────────────
async def claim_free_query(agent_key: str, day: str, cap: int) -> Optional[dict]:
    r = await _rpc("crypto_claim_free_query",
                   {"p_agent_key": agent_key, "p_day": day, "p_cap": cap})
    if isinstance(r, dict) and "allowed" in r:
        return r
    if isinstance(r, list) and r and isinstance(r[0], dict):
        return r[0]
    logger.warning(f"claim_free_query rpc unexpected: {r}")
    return None


# ── payment ledger ────────────────────────────────────────────────────────────
async def payment_tx_used(tx_signature: str) -> bool:
    rows = await _select("crypto_payments",
                         {"tx_signature": f"eq.{tx_signature}", "select": "tx_signature", "limit": "1"})
    return bool(rows)


async def insert_payment(row: dict) -> dict:
    if not configured():
        return {"error": "not_configured"}
    r = await request_json("POST", _url("crypto_payments"),
                           headers=_headers({"Prefer": "return=minimal"}),
                           body=row, timeout=config.REQUEST_TIMEOUT)
    if isinstance(r, list):
        return {"data": r}
    if isinstance(r, dict) and "error" not in r:
        return {"data": [r]}
    return r if isinstance(r, dict) else {"error": "bad_response", "detail": str(r)}
