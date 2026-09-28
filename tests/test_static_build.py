"""Static demo build: offline capture, CSP wiring and no outbound references in dist/."""

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_builder():
    spec = importlib.util.spec_from_file_location("build_static", ROOT / "scripts/build_static.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_static_build_is_self_contained(tmp_path):
    builder = load_builder()
    out = tmp_path / "dist"
    data = builder.build(out)

    index = (out / "index.html").read_text()
    headers = (out / "_headers").read_text()
    meta = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', index).group(1)
    assert meta == "; ".join(builder.CSP)
    assert "  Content-Security-Policy: " + meta + "; frame-ancestors 'none'\n" in headers
    assert "script-src 'self'" in meta and "connect-src 'self'" in meta and "unsafe" not in meta
    assert index.index("assets/static-api.js") < index.index("assets/ui-utils.js")
    assert 'src="/' not in index and 'href="/' not in index

    for file in out.rglob("*"):
        if file.is_file():
            assert not re.search(r"https?://", file.read_text()), file.name

    assert data["get"]["/api/runtime"]["csrf"] == "static-demo"
    assert data["get"]["/api/gaps"]["dry_run"] is True
    assert data["swap"], "a DRY_RUN flow is captured"
    for flow in data["swap"].values():
        assert flow["final"]["status"] == "dry_run_complete"
        assert flow["final"]["dry_run"] is True
        assert flow["final"]["transactions"] == []
    # static-api.js looks detail quotes up with String(Number(amount)).
    assert all(key.endswith("|USDT|1000") for key in data["detail"])
    addresses = {r["address"] for g in data["get"]["/api/gaps"]["coin_groups"] for r in g["routes"]}
    assert all(a.startswith("0xde70") for a in addresses), "demo contracts are visibly synthetic"
    assert json.loads((out / "data/demo.json").read_text()) == data
