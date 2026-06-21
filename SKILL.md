---
name: foundrynet-crypto-intelligence
description: Real-time crypto prices, market overview, DeFi metrics, and whale-movement alerts from the FoundryNet Data Network
---

# FoundryNet Crypto Intelligence

## Connect
```bash
claude mcp add --transport http foundrynet-crypto https://crypto-intel-mcp-production.up.railway.app/mcp
```

## Available Tools
- `price` (free) — Real-time coin price + 24h stats (e.g. coin=BTC)
- `market_overview` (free) — Top coins, market cap, BTC dominance
- `price_history` ($0.01) — Historical OHLC price series
- `whale_alerts` ($0.01) — Large on-chain movements
- `defi_overview` ($0.01) — TVL and protocol metrics
- `anomaly_scan` ($0.02) — Unusual price/volume patterns
- `daily_brief` ($15) — Curated daily crypto intelligence, MINT-attested
- `mint_info` (free) — Network + attestation info

A daily free-tier allowance precedes the paywall; paid tools settle in USDC on
Solana (x402) **or** Stripe. An `Authorization: Bearer fnet_…` key bypasses the gate.

## Part of the FoundryNet Data Network
17 interconnected data-intelligence servers with MINT-attested, verifiable outputs.
Live network activity: https://mint.foundrynet.io/feed
