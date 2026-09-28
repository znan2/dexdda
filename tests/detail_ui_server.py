"""M4 browser fixture. ETH is a synthetic deposit network on chain 10 here only."""

from detail_fixtures import attach_detail
from ui_server import collector

from app.main import create_app
from app.registry.models import ChainMap

detail, adapter, state = attach_detail(collector)
detail.chain_map = ChainMap(
    networks=[
        {
            "exchange": ex,
            "net_type": "ETH",
            "chain_index": "10",
            "platform_id": "fixture",
            "evidence": "offline fixture",
        }
        for ex in ("upbit", "bithumb")
    ]
)
app = create_app(collector, detail, enable_execution=False, enable_operations=False)
