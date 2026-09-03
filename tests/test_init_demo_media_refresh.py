import argparse
import json
import sys
import types

import scripts.init_demo as init_demo


def test_run_fetches_media_when_force_refresh_set(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "product_media.json").write_text(json.dumps({"SKU1": {"image_url": "x"}}))

    seen = {"fetched": False}

    fake_csv_module = types.ModuleType("scripts.csv_to_graph")
    fake_csv_module.run = lambda ns: None
    monkeypatch.setitem(sys.modules, "scripts.csv_to_graph", fake_csv_module)

    fake_fetch_module = types.ModuleType("scripts.fetch_product_media")
    def fake_fetch_main():
        seen["fetched"] = True
    fake_fetch_module.main = fake_fetch_main
    monkeypatch.setitem(sys.modules, "scripts.fetch_product_media", fake_fetch_module)

    monkeypatch.setattr(init_demo, "_count", lambda graph, query: 25)

    class FakeGraph:
        def query(self, *args, **kwargs):
            return types.SimpleNamespace(result_set=[])

    monkeypatch.setattr(init_demo, "_get_graph", lambda args: FakeGraph())

    args = argparse.Namespace(
        host="localhost",
        port=6379,
        graph="dotandkey",
        force_media_refresh=True,
    )
    init_demo.run(args)

    assert seen["fetched"] is True
