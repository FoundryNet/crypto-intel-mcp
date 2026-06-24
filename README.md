# Crypto Intel MCP — DeFi & Crypto Risk Scanner

Part of the **FoundryNet Data Network**.

DeFi and crypto risk scanning for agents — score any token for market cap risk,
liquidity concerns, volatility flags, and market sentiment. Also provides free
`price` + `market_overview`, plus paid `price_history`, `whale_alerts`,
`defi_overview`, `anomaly_scan` and `token_risk_scan`. Data from CoinGecko +
DeFiLlama + the alternative.me Fear & Greed index (all keyless), refreshed every
15 minutes into a standalone Supabase project.

## Tools

| Tool | Price | Description |
| --- | --- | --- |
| price | free | Latest USD price for a coin (id or symbol) |
| market_overview | free | Top coins, total market cap, BTC dominance, Fear & Greed |
| price_history | $0.01 | Daily price/volume history for a coin |
| whale_alerts | $0.01 | Volume-derived notable flows / turnover signals |
| defi_overview | $0.01 | DeFi TVL by protocol/category (DeFiLlama) |
| anomaly_scan | $0.02 | Unusual volume, large moves, MA divergence (attested) |
| token_risk_scan | $0.02 | DeFi risk score 0-100 — market cap, liquidity, volatility & sentiment flags + recommendation (attested) |
| daily_brief | $15 | Curated daily crypto movers brief |

## Live network activity

**Live feed:** [mint.foundrynet.io/feed](https://mint.foundrynet.io/feed)  
Real-time verified work across 13 servers and autonomous agents, anchored on Solana via [MINT Protocol](https://mint.foundrynet.io).
