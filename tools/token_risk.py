from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def token_risk_scan(
        coin: str,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """DeFi Risk Scanner — score any token 0-100 for downside risk by combining
        its live price with the broader market read. Flags micro/small-cap risk, thin
        liquidity (24h volume vs market cap), extreme volatility (24h move) and an
        extreme-fear market regime, then returns a risk_level and a plain-English
        recommendation.

        PAID: $0.02 USDC per scan after the daily free allowance (50/day). On a 402,
        pay the returned Solana memo and re-call with the SAME args plus
        payment_tx=<signature>. An Authorization: Bearer fnet_ key bypasses it.

        Args:
            coin: a CoinGecko id (e.g. "bitcoin") or a symbol (e.g. "BTC").
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: Solana tx signature, when re-calling after a 402.
        """
        return await core.do_token_risk_scan(
            coin,
            agent_key=identity.resolve_agent_key(agent_id),
            payment_tx=payment_tx, api_key=identity.bearer())
