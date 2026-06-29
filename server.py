"""crypto-intel-mcp — crypto market intelligence for agents.

A FastMCP server over its OWN standalone Supabase project. `price` and
`market_overview` are FREE (the loss leader — huge volume, every call surfaces the
FoundryNet network); `price_history`, `whale_alerts`, `defi_overview` and
`anomaly_scan` are paid via x402. Markets, DeFi TVL, OHLCV history and the Fear &
Greed index are aggregated every 15 minutes from CoinGecko + DeFiLlama +
alternative.me (all keyless).

  price            — latest USD price for a coin (id or symbol)   (free)
  market_overview  — top coins, total cap, BTC dominance, F&G      (free)
  price_history    — daily price/volume history                    ($0.01)
  whale_alerts     — volume-derived notable flows / turnover        ($0.01)
  defi_overview    — DeFi TVL by protocol/category                  ($0.01)
  anomaly_scan     — unusual volume, moves, MA divergence            ($0.02)
  daily_brief      — curated daily crypto movers brief              ($15)
  mint_info        — FoundryNet Data Network + MINT cross-promo     (free)

Paid-tool free tier 50 queries/day per agent, then x402 (USDC on Solana). Bearer
fnet_ key bypasses. Transport: Streamable HTTP at /mcp (+ legacy /sse). Health: /health.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging

from fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

import config
import core
import crypto_aggregator as agg
import daily_curator
import event_log
import identity
import payment_gate
import x402_standard
import supa
import tools

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("cry.mcp")

if not supa.configured():
    logger.warning("SUPABASE_SERVICE_KEY not set — market data served live per call, nothing cached.")

mcp = FastMCP("crypto-intel")

if payment_gate.is_active():
    logger.info(f"pay-per-query ARMED → {config.PAYMENT_RECIPIENT} after "
                f"{config.FREE_TIER_DAILY}/day free (price/market_overview always free; "
                f"history=${config.PRICE_PRICE_HISTORY}, whale=${config.PRICE_WHALE_ALERTS}, "
                f"defi=${config.PRICE_DEFI_OVERVIEW}, anomaly=${config.PRICE_ANOMALY_SCAN})")
else:
    logger.info("pay-per-query INERT (X402 off or recipient unset) — all tools free")

tools.register_all(mcp)


# ── okf-reliability-v1: emit reliability metadata on every tool result (#2964) ──
try:
    from okf_middleware import ReliabilityMiddleware
    mcp.add_middleware(ReliabilityMiddleware(server_id="crypto-intel"))
except Exception as _okf_e:  # noqa: BLE001
    import logging as _okf_log; _okf_log.getLogger(__name__).warning(f"okf middleware not wired: {_okf_e}")


@mcp.custom_route("/v1/reliability", methods=["GET"])
async def _okf_reliability_route(request):
    from starlette.responses import JSONResponse
    import okf_endpoint
    return JSONResponse(okf_endpoint.reliability_payload("crypto-intel"))


# ── Health ──────────────────────────────────────────────────────────────────
@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({
        "status": "ok", "service": "crypto-intel-mcp", "transport": "streamable-http",
        "network": "FoundryNet Data Network",
        "tools": ["price", "market_overview", "price_history", "whale_alerts",
                  "defi_overview", "anomaly_scan", "token_risk_scan", "daily_brief",
                  "mint_info"],
        "dataset": "supabase:crypto_prices" if supa.configured() else "unconfigured",
        "data_source": "CoinGecko + DeFiLlama + alternative.me (Fear & Greed)",
        "agg_interval_minutes": config.AGG_INTERVAL_MINUTES,
        "x402_enabled": config.X402_ENABLED,
        "query_payment": "armed" if payment_gate.is_active() else "free",
        "prices_usdc": {"price": 0, "market_overview": 0,
                        "price_history": config.PRICE_PRICE_HISTORY,
                        "whale_alerts": config.PRICE_WHALE_ALERTS,
                        "defi_overview": config.PRICE_DEFI_OVERVIEW,
                        "anomaly_scan": config.PRICE_ANOMALY_SCAN,
                        "token_risk_scan": config.PRICE_TOKEN_RISK,
                        "daily_brief": config.PRICE_DAILY_BRIEF},
        "free_tier_daily": config.FREE_TIER_DAILY,
        "payment_recipient": config.PAYMENT_RECIPIENT,
    })


@mcp.custom_route("/ping", methods=["GET"])
async def ping(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


# ── REST surface ─────────────────────────────────────────────────────────────
_ERR_STATUS = {"bad_request": 400, "not_configured": 503, "not_found": 404,
               "payment_required": 402, "not_available": 404}


def _resp(d: dict) -> JSONResponse:
    if "error" not in d:
        return JSONResponse(d, status_code=200)
    err = str(d.get("error") or "")
    code = _ERR_STATUS.get(err, 502 if err in ("network", "non_json_response", "unreachable") else 400)
    if err.startswith("http_") and err[5:].isdigit():
        code = int(err[5:])
    return JSONResponse(d, status_code=code)


async def _json_body(request: Request) -> dict:
    try:
        b = await request.json()
        return b if isinstance(b, dict) else {}
    except Exception:
        return {}


def _akey(request: Request, body: dict) -> str:
    return identity.resolve_agent_key(body.get("agent_id"), request=request)


def _coin(b: dict, qp) -> str:
    return b.get("coin") or b.get("symbol") or b.get("id") or qp.get("coin") or qp.get("symbol") or ""


@mcp.custom_route("/v1/price", methods=["GET", "POST"])
async def rest_price(request: Request) -> JSONResponse:
    b = await _json_body(request)
    return _resp(await core.do_price(_coin(b, request.query_params)))


@mcp.custom_route("/v1/market-overview", methods=["GET", "POST"])
async def rest_market_overview(request: Request) -> JSONResponse:
    return _resp(await core.do_market_overview())


@mcp.custom_route("/v1/history", methods=["POST"])
async def rest_history(request: Request) -> JSONResponse:
    b = await _json_body(request)
    return _resp(await core.do_price_history(_coin(b, request.query_params), b.get("days", 30),
                                             agent_key=_akey(request, b),
                                             payment_tx=b.get("payment_tx"),
                                             api_key=identity.bearer(request)))


@mcp.custom_route("/v1/whale-alerts", methods=["POST"])
async def rest_whale_alerts(request: Request) -> JSONResponse:
    b = await _json_body(request)
    return _resp(await core.do_whale_alerts(b.get("coin"), b.get("min_value_usd"),
                                            agent_key=_akey(request, b),
                                            payment_tx=b.get("payment_tx"),
                                            api_key=identity.bearer(request)))


@mcp.custom_route("/v1/defi", methods=["POST"])
async def rest_defi(request: Request) -> JSONResponse:
    b = await _json_body(request)
    return _resp(await core.do_defi_overview(b.get("protocol"),
                                             agent_key=_akey(request, b),
                                             payment_tx=b.get("payment_tx"),
                                             api_key=identity.bearer(request)))


@mcp.custom_route("/v1/anomaly-scan", methods=["POST"])
async def rest_anomaly_scan(request: Request) -> JSONResponse:
    b = await _json_body(request)
    return _resp(await core.do_anomaly_scan(b.get("coin"),
                                            agent_key=_akey(request, b),
                                            payment_tx=b.get("payment_tx"),
                                            api_key=identity.bearer(request)))


@mcp.custom_route("/v1/token-risk-scan", methods=["POST"])
async def rest_token_risk_scan(request: Request) -> JSONResponse:
    b = await _json_body(request)
    return _resp(await core.do_token_risk_scan(_coin(b, request.query_params),
                                               agent_key=_akey(request, b),
                                               payment_tx=b.get("payment_tx"),
                                               api_key=identity.bearer(request)))


@mcp.custom_route("/v1/daily-brief", methods=["POST"])
async def rest_daily_brief(request: Request) -> JSONResponse:
    b = await _json_body(request)
    return _resp(await core.do_daily_brief(b.get("date"), agent_key=_akey(request, b),
                                           payment_tx=b.get("payment_tx"),
                                           api_key=identity.bearer(request)))


@mcp.custom_route("/v1/mint-info", methods=["GET", "POST"])
async def rest_mint(request: Request) -> JSONResponse:
    return JSONResponse(core.mint_info())


# ── Discovery ────────────────────────────────────────────────────────────────
_AGENT_CARD = {
    "name": "Crypto Market Intelligence MCP",
    "description": ("DeFi and crypto risk scanning — score any token for market cap "
                    "risk, liquidity concerns, volatility flags, and market sentiment. "
                    "Also provides real-time prices, DeFi TVL, and whale alerts."),
    "url": config.PUBLIC_MCP_URL,
    "version": "1.0.0",
    "capabilities": {"tools": ["price", "market_overview", "price_history",
                               "whale_alerts", "defi_overview", "anomaly_scan",
                               "token_risk_scan", "daily_brief", "mint_info"]},
    "provider": {"name": "FoundryNet", "url": "https://foundrynet.io"},
    "network": "FoundryNet Data Network",
    "attestation": {"protocol": "MINT Protocol",
                    "endpoint": "https://mint-mcp-production.up.railway.app/mcp",
                    "verified_outputs": True, "live_feed": "https://mint.foundrynet.io/feed", "feed_api": "https://mint-mcp-production.up.railway.app/v1/feed"},
    "protocols": {"mcp": {"endpoint": config.PUBLIC_MCP_URL, "transport": "streamable-http", "tools_count": 9},
                  "x402": {"supported": True, "currency": "USDC", "network": "solana"}},
    "contact": "hello@foundrynet.io",
}


@mcp.custom_route("/.well-known/agent-card.json", methods=["GET"])
async def agent_card(request: Request) -> JSONResponse:
    return JSONResponse(_AGENT_CARD, headers={"Cache-Control": "public, max-age=300"})


@mcp.custom_route("/.well-known/mcp", methods=["GET"])
async def mcp_endpoints(request: Request) -> JSONResponse:
    return JSONResponse({"endpoints": [{"url": config.PUBLIC_MCP_URL,
                                        "transport": "streamable-http",
                                        "name": "Crypto Market Intelligence MCP"}]},
                        headers={"Cache-Control": "public, max-age=300"})


async def _live_tools() -> list:
    res = mcp.list_tools()
    if inspect.iscoroutine(res):
        res = await res
    return [{"name": t.name, "description": (getattr(t, "description", "") or "").strip(),
             "inputSchema": getattr(t, "parameters", None) or {"type": "object"}} for t in res]


@mcp.custom_route("/.well-known/mcp/server-card.json", methods=["GET"])
async def server_card(request: Request) -> JSONResponse:
    live = await _live_tools()
    return JSONResponse({
        "serverInfo": {"name": "Crypto Market Intelligence MCP", "version": "1.0.0"},
        "authentication": {"type": "http", "scheme": "bearer",
                           "description": ("price, market_overview and mint_info are free; "
                                           "price_history, whale_alerts, defi_overview and "
                                           "anomaly_scan give 50 free queries/day then take an "
                                           "fnet_ Bearer key OR x402 USDC.")},
        "tools": live, "version": "1.0", "name": "Crypto Market Intelligence MCP",
        "tagline": "DeFi token risk scoring + free crypto prices, DeFi TVL & anomaly scans for agents.",
        "description": ("DeFi and crypto risk scanning — score any token for market cap risk, "
                        "liquidity concerns, volatility flags, and market sentiment. Also provides "
                        "real-time prices, DeFi TVL, and whale alerts. CoinGecko + DeFiLlama + "
                        "Fear & Greed, refreshed every 15 minutes. The free price gateway every "
                        "trading agent needs."),
        "serverUrl": config.PUBLIC_MCP_URL, "transport": "streamable-http",
        "tools_count": len(live),
        "categories": ["finance", "crypto", "data", "defi", "trading"],
        "keywords": ["crypto", "bitcoin", "ethereum", "defi", "price", "market cap",
                     "whale alerts", "fear and greed", "anomaly detection",
                     "defi-risk", "token-analysis", "crypto-risk-score",
                     "rug-pull-detection", "token-safety"],
        "network": "FoundryNet Data Network", "see_also": config.SISTER_SERVERS,
        "pricing": {"model": "metered",
                    "free_tier": "price + market_overview are free; 50 paid queries/day per agent",
                    "paid_from": f"{config.PRICE_PRICE_HISTORY} USDC per query (x402)"},
    }, headers={"Cache-Control": "public, max-age=300"})


# ── Entrypoint ───────────────────────────────────────────────────────────────
_FREE_TOOL_NAMES = {"mint_info", "price", "market_overview"}


@mcp.custom_route("/.well-known/mcp.json", methods=["GET"])
async def wellknown_mcp_json(request: Request) -> JSONResponse:
    """Machine-discovery card (emerging standard) for AI clients/crawlers."""
    live = await _live_tools()
    names = [t["name"] for t in live]
    return JSONResponse({
        "name": _AGENT_CARD["name"],
        "description": _AGENT_CARD["description"],
        "url": config.PUBLIC_MCP_URL,
        "transport": ["streamable-http"],
        "tools": names,
        "pricing": {"model": "per-query", "free_tier": True,
                    "paid_tools": [n for n in names if n not in _FREE_TOOL_NAMES]},
        "attestation": {"enabled": True, "protocol": "MINT Protocol",
                        "feed": "https://mint.foundrynet.io/feed"},
        "network": {"name": "FoundryNet Data Network", "servers": 17,
                    "homepage": "https://foundrynet.io"},
    }, headers={"Cache-Control": "public, max-age=300"})



# ── Standard x402 compliance (discoverable on x402scan / 402 Index / CDP Bazaar) ──
@mcp.custom_route("/x402", methods=["GET"])
async def x402_index(request: Request) -> JSONResponse:
    return JSONResponse(x402_standard.index(),
                        headers={"Cache-Control": "public, max-age=300",
                                 "Access-Control-Allow-Origin": "*"})


@mcp.custom_route("/.well-known/x402", methods=["GET"])
async def x402_wellknown(request: Request) -> JSONResponse:
    return JSONResponse(x402_standard.index(),
                        headers={"Cache-Control": "public, max-age=300",
                                 "Access-Control-Allow-Origin": "*"})


@mcp.custom_route("/x402/{tool}", methods=["GET", "POST"])
async def x402_resource(request: Request) -> JSONResponse:
    tool = request.path_params["tool"]
    if tool not in x402_standard.PAID_TOOLS:
        return JSONResponse({"error": "unknown_resource", "tool": tool,
                             "available": list(x402_standard.PAID_TOOLS)}, status_code=404)
    challenge = x402_standard.payment_required_header(tool)
    return JSONResponse(x402_standard.payment_required(tool), status_code=402,
                        headers={"Cache-Control": "public, max-age=300",
                                 "Access-Control-Allow-Origin": "*",
                                 "PAYMENT-REQUIRED": challenge,
                                 "X-PAYMENT": challenge,
                                 "Link": '</openapi.json>; rel="describedby"',
                                 "WWW-Authenticate": 'x402 version="2"'})


@mcp.custom_route("/openapi.json", methods=["GET"])
async def openapi_doc(request: Request) -> JSONResponse:
    """OpenAPI 3.1 discovery doc — x402scan requires a spec at a discoverable URL."""
    return JSONResponse(x402_standard.openapi(),
                        headers={"Cache-Control": "public, max-age=300",
                                 "Access-Control-Allow-Origin": "*",
                                 "Link": '</openapi.json>; rel="describedby"'})


def build_dual_app():
    main_app = mcp.http_app(transport="http", path="/mcp")
    sse_app = mcp.http_app(transport="sse", path="/sse")
    for r in sse_app.routes:
        if getattr(r, "path", None) in ("/sse", "/messages"):
            main_app.router.routes.append(r)
    main_life, sse_life = main_app.router.lifespan_context, sse_app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _dual_lifespan(app):
        async with main_life(app):
            async with sse_life(app):
                agg_task = asyncio.create_task(agg.agg_loop())
                brief_task = asyncio.create_task(daily_curator.curator_loop())
                try:
                    yield
                finally:
                    for t in (agg_task, brief_task):
                        t.cancel()
                        with contextlib.suppress(Exception):
                            await t
    main_app.router.lifespan_context = _dual_lifespan
    # Per-call telemetry: times every /v1/* request and fire-and-forgets it to the
    # agents event-log ingest. Never blocks or raises into the request path.
    main_app.add_middleware(BaseHTTPMiddleware, dispatch=event_log.middleware)
    return main_app


if __name__ == "__main__":
    import uvicorn
    logger.info(f"crypto-intel-mcp starting on 0.0.0.0:{config.PORT} "
                f"(dataset={'supabase' if supa.configured() else 'off'}, x402={config.X402_ENABLED})")
    uvicorn.run(build_dual_app(), host="0.0.0.0", port=config.PORT, log_level="warning")
