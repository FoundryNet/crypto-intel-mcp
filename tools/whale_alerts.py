from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def whale_alerts(
        coin: Optional[str] = None,
        min_value_usd: Optional[float] = None,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Surface notable crypto flows — coins with unusually large 24h trading
        volume and high turnover (volume vs market cap), a proxy for whale activity.
        NOTE: this is VOLUME-DERIVED (method="volume_derived"), not raw on-chain
        transfer data, which requires a paid whale-tracking API.

        PAID: $0.01 USDC per query after a daily free allowance. On a 402, pay the
        returned Solana memo and re-call with the SAME args plus payment_tx=<signature>.
        An Authorization: Bearer fnet_ key bypasses payment.

        Args:
            coin: optional CoinGecko id or symbol to filter to one coin.
            min_value_usd: optional minimum 24h volume (USD) floor.
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: Solana tx signature, when re-calling after a 402.
        """
        return await core.do_whale_alerts(coin, min_value_usd,
                                          agent_key=identity.resolve_agent_key(agent_id),
                                          payment_tx=payment_tx, api_key=identity.bearer())
