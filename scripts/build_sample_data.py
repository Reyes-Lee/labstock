"""產生範例（測試用）資料到 data/sample/。

範例資料為常見的生物實驗室試劑與耗材，數量與日期皆為「測試用的假設值」，
不是受訪者實驗室的真實庫存；操作者也以「示範使用者」代稱（去識別化）。
基準日期固定為 2026-11-27，讓示範與測試結果可重現。

執行：python scripts/build_sample_data.py
"""
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from labstock.inventory import Inventory  # noqa: E402
from labstock.storage import Storage  # noqa: E402

OUT = ROOT / "data" / "sample"
TODAY = date(2026, 11, 27)

REAGENTS = [
    # 名稱, 單位, 安全量, 位置, 類別, 廠商
    ("Taq DNA Polymerase", "μL", 100, "-20°C 冰箱 A 層", "酵素", "廠商甲"),
    ("10X PBS 緩衝液", "mL", 200, "室溫試劑櫃", "緩衝液", "廠商乙"),
    ("50 mL 離心管", "支", 50, "耗材櫃 2", "耗材", "廠商丙"),
    ("1.5 mL 微量離心管", "支", 200, "耗材櫃 1", "耗材", "廠商丙"),
    ("Agarose 洋菜膠", "g", 20, "室溫試劑櫃", "粉末試劑", "廠商甲"),
    ("DMEM 培養基", "mL", 500, "4°C 冰箱", "培養基", "廠商丁"),
    ("胎牛血清 FBS", "mL", 50, "-20°C 冰箱 B 層", "血清", "廠商丁"),
    ("Trypsin-EDTA", "mL", 20, "-20°C 冰箱 B 層", "酵素", "廠商丁"),
    ("95% 乙醇", "mL", 1000, "防爆櫃", "溶劑", "廠商乙"),
    ("SDS 十二烷基硫酸鈉", "g", 10, "室溫試劑櫃", "粉末試劑", "廠商乙"),
]

# 試劑編號, 瓶數, 每瓶容量, 效期, 入庫日
STOCK_IN = [
    ("R001", 2, 500, "2027-06-30", "2026-06-10"),
    ("R002", 3, 500, "2028-01-31", "2026-09-15"),
    ("R003", 4, 25, "2030-12-31", "2026-08-20"),
    ("R004", 2, 500, "2030-12-31", "2026-08-20"),
    ("R005", 1, 100, "2028-05-31", "2026-07-01"),
    ("R006", 2, 500, "2026-12-15", "2026-10-01"),
    ("R007", 1, 50, "2026-11-20", "2025-09-01"),   # 這瓶已過期，用來示範過期判斷
    ("R007", 2, 50, "2027-08-31", "2026-08-01"),
    ("R008", 2, 100, "2026-12-20", "2026-09-20"),
    ("R009", 2, 2500, "2029-03-31", "2026-05-05"),
    ("R010", 1, 100, "2029-12-31", "2026-04-01"),
]

# 試劑編號, 總用量, 操作者（依先進先出自動分配到各瓶）
USAGE = [
    ("R001", 880, "示範使用者A"), ("R002", 650, "示範使用者B"), ("R003", 30, "示範使用者A"),
    ("R004", 820, "示範使用者B"), ("R005", 65, "示範使用者A"), ("R006", 200, "示範使用者B"),
    ("R007", 20, "示範使用者A"), ("R008", 170, "示範使用者B"), ("R009", 4300, "示範使用者A"),
]


def consume(inv: Inventory, rid: str, total: float, user: str) -> None:
    left = total
    while left > 0:
        pick = inv.suggest_bottle(rid)
        take = min(left, float(pick.remaining_qty))
        inv.use(rid, take, user, pick.bottle_id)
        left -= take


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    inv = Inventory(Storage(OUT), today=TODAY)
    for name, unit, safety, loc, cat, vendor in REAGENTS:
        inv.add_reagent(name, unit, safety, loc, cat, vendor, user="示範管理者")
    for rid, n, q, exp, rec in STOCK_IN:
        inv.stock_in(rid, n, q, exp, user="示範管理者", received_date=rec)
    for rid, total, user in USAGE:
        consume(inv, rid, total, user)
    shutil.rmtree(OUT / "backup", ignore_errors=True)
    print(inv.summary()[["reagent_id", "name", "total", "unit", "safety_stock", "flag"]].to_string())
    print(f"\n範例資料已輸出到 {OUT}")


if __name__ == "__main__":
    main()
