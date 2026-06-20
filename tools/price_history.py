from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def price_history(
        coin: str,
        days: Optional[int] = 30,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Get the daily price/volume history for a cryptocurrency over the last N
        days — for backtesting, charting, and trend analysis.

        PAID: $0.01 USDC per query after a daily free allowance. On a 402, pay the
        returned Solana memo and re-call with the SAME args plus payment_tx=<signature>.
        An Authorization: Bearer fnet_ key bypasses payment.

        Args:
            coin: CoinGecko id (e.g. "bitcoin") or symbol (e.g. "BTC").
            days: lookback window in days (1–365, default 30).
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: Solana tx signature, when re-calling after a 402.
        """
        return await core.do_price_history(coin, days,
                                           agent_key=identity.resolve_agent_key(agent_id),
                                           payment_tx=payment_tx, api_key=identity.bearer())
