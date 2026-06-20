from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def defi_overview(
        protocol: Optional[str] = None,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Get DeFi Total Value Locked (TVL) intelligence from DeFiLlama. With a
        protocol name, returns that protocol's TVL and 1d/7d changes; otherwise
        returns the top protocols by TVL grouped by category, plus total DeFi TVL.

        PAID: $0.01 USDC per query after a daily free allowance. On a 402, pay the
        returned Solana memo and re-call with the SAME args plus payment_tx=<signature>.
        An Authorization: Bearer fnet_ key bypasses payment.

        Args:
            protocol: optional protocol name (e.g. "Aave", "Lido") for a single read.
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: Solana tx signature, when re-calling after a 402.
        """
        return await core.do_defi_overview(protocol,
                                           agent_key=identity.resolve_agent_key(agent_id),
                                           payment_tx=payment_tx, api_key=identity.bearer())
