"""匯出模組（F6）：把目前庫存匯出成中文欄位的 CSV 報表，Excel 可直接開啟。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .inventory import Inventory

HEADERS = {
    "reagent_id": "編號", "name": "試劑名稱", "category": "類別", "unit": "單位", "container": "包裝單位",
    "location": "存放位置", "vendor": "廠商", "total": "可用總量", "safety_stock": "安全庫存量",
    "bottles": "在庫包裝數", "nearest_expiry": "最近效期", "last_received": "最近入庫日", "flag": "狀態",
}


def export_report(inv: Inventory, out_path: Path | str) -> Path:
    df = inv.summary().rename(columns=HEADERS)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    return out


def export_bottles(inv: Inventory, out_path: Path | str) -> Path:
    """逐件包裝明細（含先進先出順序），方便盤點時列印。"""
    s = inv.summary()
    frames = []
    for rid, name in zip(s.reagent_id, s["name"]):
        b = inv.bottles_of(rid)
        if not b.empty:
            b = b.assign(name=name, fifo=range(1, len(b) + 1))
            frames.append(b)
    cols = {"name": "試劑名稱", "bottle_no": "序號", "fifo": "建議使用順序", "remaining_qty": "剩量",
            "received_date": "入庫日", "expiry_date": "效期", "days_left": "距到期天數"}
    df = pd.concat(frames)[list(cols)] if frames else pd.DataFrame(columns=list(cols))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.rename(columns=cols).to_csv(out, index=False, encoding="utf-8-sig")
    return out
