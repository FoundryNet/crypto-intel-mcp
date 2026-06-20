from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def anomaly_scan(
        coin: Optional[str] = None,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Scan the market for anomalies — unusual volume spikes (24h volume vs
        market cap), large 24h price moves, and price divergence from the 7d/30d
        moving averages. Returns ranked anomalies with a type and severity. The
        result carries a MINT provenance attestation so a buyer can verify it.

        PAID: $0.02 USDC per query after a daily free allowance. On a 402, pay the
        returned Solana memo and re-call with the SAME args plus payment_tx=<signature>.
        An Authorization: Bearer fnet_ key bypasses payment.

        Args:
            coin: optional CoinGecko id or symbol to scan a single coin (default: market-wide).
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: Solana tx signature, when re-calling after a 402.
        """
        return await core.do_anomaly_scan(coin,
                                          agent_key=identity.resolve_agent_key(agent_id),
                                          payment_tx=payment_tx, api_key=identity.bearer())
