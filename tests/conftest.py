import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from labstock.inventory import Inventory  # noqa: E402
from labstock.storage import Storage  # noqa: E402

TODAY = date(2026, 11, 27)


@pytest.fixture
def inv(tmp_path):
    return Inventory(Storage(tmp_path / "data"), today=TODAY)


@pytest.fixture
def taq(inv):
    """已建立 Taq（安全量 100 μL），入庫 3 瓶 × 50 μL。"""
    rid = inv.add_reagent("Taq DNA Polymerase", "μL", 100, "-20°C 冰箱", "酵素", user="測試")
    inv.stock_in(rid, 3, 50, "2027-06-30", user="測試", received_date="2026-11-01")
    return rid
