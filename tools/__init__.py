"""crypto-intel-mcp tools — one per file.

  price            (free)    latest price for a coin (id or symbol)
  market_overview  (free)    top coins, total market cap, BTC dominance, fear & greed
  price_history    ($0.01)   daily price/volume history for a coin
  whale_alerts     ($0.01)   volume-derived notable flows / turnover signals
  defi_overview    ($0.01)   DeFi TVL by protocol/category (DeFiLlama)
  anomaly_scan     ($0.02)   unusual volume, large moves, MA divergence (attested)
  daily_brief      ($15)     curated daily crypto movers brief
  mint_info        (free)    FoundryNet Data Network + MINT cross-promo
"""
from . import price as price_tool
from . import market_overview as market_overview_tool
from . import price_history as price_history_tool
from . import whale_alerts as whale_alerts_tool
from . import defi_overview as defi_overview_tool
from . import anomaly_scan as anomaly_scan_tool
from . import daily_brief as daily_brief_tool
from . import mint as mint_tool


def register_all(mcp) -> None:
    price_tool.register(mcp)
    market_overview_tool.register(mcp)
    price_history_tool.register(mcp)
    whale_alerts_tool.register(mcp)
    defi_overview_tool.register(mcp)
    anomaly_scan_tool.register(mcp)
    daily_brief_tool.register(mcp)
    mint_tool.register(mcp)
