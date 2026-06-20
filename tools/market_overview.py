import core


def register(mcp) -> None:
    @mcp.tool
    async def market_overview() -> dict:
        """Get a snapshot of the crypto market. FREE. Returns the top 20 coins by
        market cap (with 24h change), total market cap, BTC dominance, and the
        current Fear & Greed index — everything an agent needs to size up sentiment
        in one call.
        """
        return await core.do_market_overview()
