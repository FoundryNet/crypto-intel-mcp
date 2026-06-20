import core


def register(mcp) -> None:
    @mcp.tool
    async def price(coin: str) -> dict:
        """Get the latest USD price for a cryptocurrency. FREE — the gateway tool
        every trading agent needs. Returns the price, 24h change, 24h volume, and
        market cap. Accepts a CoinGecko id ("bitcoin") OR a ticker symbol ("BTC").

        Args:
            coin: CoinGecko id (e.g. "bitcoin", "ethereum") or symbol (e.g. "BTC").
        """
        return await core.do_price(coin)
