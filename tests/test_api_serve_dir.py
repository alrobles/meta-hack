"""Serve-dir coherence: local API default must match the deploy chain.

Canonical chain: export_serve --dst app/data -> precompute into app/data
-> deploy.sh rsyncs app/data -> container DATA_DIR=/app/frontend/data.
data/serve/ is pipeline staging (see pipeline/nightly.py), NOT the API dir.
"""
from pathlib import Path

import api.main as m


def test_default_data_dir_is_app_data():
    assert Path(str(m.DATA)).name == "data"
    assert m.DATA.parent.name == "app"
