"""資料層：以 pandas 讀寫三個 CSV 資料表，寫入前自動備份。

資料表：
- reagents.csv   試劑品項（名稱、單位、位置、安全庫存量…）
- bottles.csv    每一瓶／每一包裝（瓶號、剩量、入庫日、效期…）
- usage_log.csv  操作紀錄（誰、何時、做了什麼、用量）

CSV 以 UTF-8-BOM（utf-8-sig）儲存，Excel 可直接開啟不會亂碼。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from .config import BACKUP_KEEP

TABLES: dict[str, list[str]] = {
    "reagents": ["reagent_id", "name", "category", "unit", "container", "location",
                 "safety_stock", "vendor", "active", "created_at"],
    "bottles": ["bottle_id", "reagent_id", "bottle_no", "initial_qty", "remaining_qty",
                "received_date", "expiry_date", "status"],
    "usage_log": ["log_id", "time", "user", "action", "reagent_id", "bottle_id", "qty", "note"],
}

NUMERIC = {
    "reagents": {"safety_stock": float},
    "bottles": {"bottle_no": int, "initial_qty": float, "remaining_qty": float},
    "usage_log": {"qty": float},
}


class Storage:
    """負責 CSV 的讀取、寫入與備份；其他模組只透過這個類別存取檔案。"""

    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir = self.data_dir / "backup"

    def path(self, table: str) -> Path:
        return self.data_dir / f"{table}.csv"

    def load(self, table: str) -> pd.DataFrame:
        cols = TABLES[table]
        p = self.path(table)
        if p.exists():
            df = pd.read_csv(p, dtype=str, encoding="utf-8-sig").fillna("")
        else:
            df = pd.DataFrame({c: pd.Series(dtype=str) for c in cols})
        for c in cols:  # 舊檔缺欄位時補上
            if c not in df.columns:
                df[c] = ""
        df = df[cols]
        for col, typ in NUMERIC[table].items():
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(typ)
        if table == "reagents":
            df["active"] = df["active"].astype(str).str.lower().isin(["true", "1", "yes"]).astype(bool)
        for c in cols:
            if c not in NUMERIC[table] and c != "active":
                df[c] = df[c].astype(str)
        if table == "reagents":  # 舊資料沒有包裝單位欄位時預設為「瓶」
            df.loc[df["container"].str.strip() == "", "container"] = "瓶"
        return df

    def save(self, table: str, df: pd.DataFrame) -> None:
        p = self.path(table)
        if p.exists():
            self._backup(table)
        tmp = p.with_suffix(".tmp")
        df[TABLES[table]].to_csv(tmp, index=False, encoding="utf-8-sig")
        tmp.replace(p)  # 先寫暫存檔再取代，避免寫到一半當機造成檔案損毀

    def _backup(self, table: str) -> None:
        self.backup_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        (self.backup_dir / f"{table}_{stamp}.csv").write_bytes(self.path(table).read_bytes())
        olds = sorted(self.backup_dir.glob(f"{table}_*.csv"))
        for old in olds[:-BACKUP_KEEP]:
            old.unlink()
