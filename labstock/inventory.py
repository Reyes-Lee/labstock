"""核心邏輯模組：試劑管理（F1）、入庫編號（F2）、領用扣量（F3）、先進先出（F6）、
低庫存與到期判斷（供 F4 使用）。

所有對外功能都是 Inventory 類別的方法，介面層（cli.py）只呼叫這些方法，
不直接碰 CSV；資料存取全部交給 storage.Storage。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from .config import EXPIRY_WARNING_DAYS
from .storage import Storage

IN_STOCK = "在庫"
EMPTY = "用完"


class InventoryError(ValueError):
    """使用者輸入不合理或操作無法執行時拋出，訊息直接顯示給使用者。"""


@dataclass
class UseResult:
    reagent_id: str
    name: str
    unit: str
    bottle_id: str
    bottle_no: int
    used: float
    bottle_remaining: float
    total_before: float
    total_after: float
    safety_stock: float
    crossed_below_safety: bool  # 這次領用讓總量「剛好跌破」安全量 → 要寄提醒


def parse_date(text: str) -> date:
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise InventoryError(f"日期格式錯誤：「{text}」，請用 YYYY-MM-DD，例如 2027-03-31")


class Inventory:
    def __init__(self, storage: Storage, today: date | None = None):
        self.storage = storage
        self.today = today or date.today()

    # ---------- 共用 ----------
    def _tables(self):
        s = self.storage
        return s.load("reagents"), s.load("bottles"), s.load("usage_log")

    def _log(self, log: pd.DataFrame, user: str, action: str, reagent_id: str,
             bottle_id: str = "", qty: float = 0, note: str = "") -> pd.DataFrame:
        row = {
            "log_id": f"L{len(log) + 1:05d}",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "user": user or "未填", "action": action, "reagent_id": reagent_id,
            "bottle_id": bottle_id, "qty": qty, "note": note,
        }
        return pd.concat([log, pd.DataFrame([row])], ignore_index=True)

    def get_reagent(self, reagent_id: str) -> pd.Series:
        reagents = self.storage.load("reagents")
        hit = reagents[(reagents.reagent_id == reagent_id) & reagents.active]
        if hit.empty:
            raise InventoryError(f"找不到試劑編號 {reagent_id}")
        return hit.iloc[0]

    # ---------- F1 試劑資料管理 ----------
    def add_reagent(self, name: str, unit: str, safety_stock: float, location: str = "",
                    category: str = "", vendor: str = "", user: str = "") -> str:
        name, unit = name.strip(), unit.strip()
        if not name:
            raise InventoryError("試劑名稱不可空白")
        if not unit:
            raise InventoryError("單位不可空白（例如 mL、g、支、管）")
        try:
            safety_stock = float(safety_stock)
        except (TypeError, ValueError):
            raise InventoryError("安全庫存量必須是數字")
        if safety_stock < 0:
            raise InventoryError("安全庫存量不可小於 0")
        reagents, _, log = self._tables()
        active = reagents[reagents.active]
        if (active["name"].str.lower() == name.lower()).any():
            raise InventoryError(f"已經有名為「{name}」的試劑，請改用入庫功能")
        new_id = f"R{len(reagents) + 1:03d}"
        row = {"reagent_id": new_id, "name": name, "category": category.strip(), "unit": unit,
               "location": location.strip(), "safety_stock": safety_stock,
               "vendor": vendor.strip(), "active": True,
               "created_at": self.today.isoformat()}
        reagents = pd.concat([reagents, pd.DataFrame([row])], ignore_index=True)
        self.storage.save("reagents", reagents)
        self.storage.save("usage_log", self._log(log, user, "新增品項", new_id, note=name))
        return new_id

    def deactivate_reagent(self, reagent_id: str, user: str = "") -> None:
        """刪除品項＝停用：從清單隱藏，但保留歷史紀錄（實作時修改的需求）。"""
        reagent = self.get_reagent(reagent_id)
        reagents, _, log = self._tables()
        reagents.loc[reagents.reagent_id == reagent_id, "active"] = False
        self.storage.save("reagents", reagents)
        self.storage.save("usage_log", self._log(log, user, "刪除品項", reagent_id, note=reagent["name"]))

    # ---------- F2 分裝入庫與瓶號 ----------
    def stock_in(self, reagent_id: str, n_bottles: int, qty_per_bottle: float,
                 expiry_date: str, user: str = "", received_date: str | None = None) -> list[str]:
        self.get_reagent(reagent_id)
        try:
            n_bottles = int(n_bottles)
            qty_per_bottle = float(qty_per_bottle)
        except (TypeError, ValueError):
            raise InventoryError("瓶數與每瓶容量必須是數字")
        if n_bottles < 1:
            raise InventoryError("瓶數至少為 1")
        if n_bottles > 200:
            raise InventoryError("一次入庫最多 200 瓶，請確認是否輸入錯誤")
        if qty_per_bottle <= 0:
            raise InventoryError("每瓶容量必須大於 0")
        exp = parse_date(expiry_date)
        rec = parse_date(received_date) if received_date else self.today
        if exp < rec:
            raise InventoryError("效期早於入庫日期，請確認效期是否輸入錯誤")

        _, bottles, log = self._tables()
        mine = bottles[bottles.reagent_id == reagent_id]
        start = int(mine.bottle_no.max()) + 1 if not mine.empty else 1  # 瓶號接續編號
        rows, ids = [], []
        for i in range(n_bottles):
            no = start + i
            bid = f"{reagent_id}-{no:03d}"
            ids.append(bid)
            rows.append({"bottle_id": bid, "reagent_id": reagent_id, "bottle_no": no,
                         "initial_qty": qty_per_bottle, "remaining_qty": qty_per_bottle,
                         "received_date": rec.isoformat(), "expiry_date": exp.isoformat(),
                         "status": IN_STOCK})
        bottles = pd.concat([bottles, pd.DataFrame(rows)], ignore_index=True)
        self.storage.save("bottles", bottles)
        log = self._log(log, user, "入庫", reagent_id, qty=n_bottles * qty_per_bottle,
                        note=f"{n_bottles} 瓶 × {qty_per_bottle:g}，瓶號 #{start}～#{start + n_bottles - 1}")
        self.storage.save("usage_log", log)
        return ids

    # ---------- 查詢與先進先出 ----------
    def bottles_of(self, reagent_id: str, include_empty: bool = False) -> pd.DataFrame:
        """依先進先出排序：效期早的在前；效期相同時入庫早、瓶號小的在前。"""
        b = self.storage.load("bottles")
        b = b[b.reagent_id == reagent_id]
        if not include_empty:
            b = b[(b.status == IN_STOCK) & (b.remaining_qty > 0)]
        b = b.assign(days_left=[(parse_date(d) - self.today).days for d in b.expiry_date])
        return b.sort_values(["expiry_date", "received_date", "bottle_no"]).reset_index(drop=True)

    def suggest_bottle(self, reagent_id: str) -> pd.Series | None:
        """建議優先使用的瓶：最早到期且「尚未過期」的一瓶。"""
        b = self.bottles_of(reagent_id)
        usable = b[b.days_left >= 0]
        return None if usable.empty else usable.iloc[0]

    def total_stock(self, reagent_id: str, bottles: pd.DataFrame | None = None) -> float:
        """可用總量：只計算未過期、在庫的瓶（過期試劑不應算進安全庫存）。"""
        b = self.storage.load("bottles") if bottles is None else bottles
        b = b[(b.reagent_id == reagent_id) & (b.status == IN_STOCK)]
        ok = [parse_date(d) >= self.today for d in b.expiry_date]
        return float(b[ok].remaining_qty.sum()) if len(b) else 0.0

    # ---------- F3 領用扣量 ----------
    def use(self, reagent_id: str, qty: float, user: str = "", bottle_id: str | None = None) -> UseResult:
        reagent = self.get_reagent(reagent_id)
        try:
            qty = float(qty)
        except (TypeError, ValueError):
            raise InventoryError("使用量必須是數字")
        if qty <= 0:
            raise InventoryError("使用量必須大於 0")

        if bottle_id is None:
            pick = self.suggest_bottle(reagent_id)
            if pick is None:
                raise InventoryError(f"「{reagent['name']}」沒有可用（未過期且有剩量）的瓶，請先入庫")
            bottle_id = pick.bottle_id

        reagents, bottles, log = self._tables()
        idx = bottles.index[(bottles.bottle_id == bottle_id) & (bottles.reagent_id == reagent_id)]
        if len(idx) == 0:
            raise InventoryError(f"找不到瓶號 {bottle_id}")
        i = idx[0]
        if bottles.at[i, "status"] != IN_STOCK or bottles.at[i, "remaining_qty"] <= 0:
            raise InventoryError(f"{bottle_id} 已經用完")
        remaining = float(bottles.at[i, "remaining_qty"])
        if qty > remaining + 1e-9:
            raise InventoryError(
                f"使用量 {qty:g} {reagent['unit']} 大於此瓶剩量 {remaining:g} {reagent['unit']}，"
                "請先用完此瓶再開下一瓶，或分兩次登錄")

        before = self.total_stock(reagent_id, bottles)
        new_remaining = round(remaining - qty, 6)
        bottles.at[i, "remaining_qty"] = new_remaining
        if new_remaining <= 0:
            bottles.at[i, "status"] = EMPTY
        after = self.total_stock(reagent_id, bottles)
        self.storage.save("bottles", bottles)
        self.storage.save("usage_log", self._log(log, user, "領用", reagent_id, bottle_id, qty))

        safety = float(reagent["safety_stock"])
        return UseResult(reagent_id, reagent["name"], reagent["unit"], bottle_id,
                         int(bottles.at[i, "bottle_no"]), qty, new_remaining, before, after, safety,
                         crossed_below_safety=(before >= safety > after))

    def use_up(self, reagent_id: str, bottle_id: str, user: str = "") -> UseResult:
        """整瓶用完（瓶子空了直接標記）。"""
        b = self.bottles_of(reagent_id)
        hit = b[b.bottle_id == bottle_id]
        if hit.empty:
            raise InventoryError(f"{bottle_id} 不在可用清單中")
        return self.use(reagent_id, float(hit.iloc[0].remaining_qty), user, bottle_id)

    # ---------- 彙總（供查詢、提醒、統計使用） ----------
    def summary(self) -> pd.DataFrame:
        reagents, bottles, _ = self._tables()
        rows = []
        for _, r in reagents[reagents.active].iterrows():
            b = bottles[(bottles.reagent_id == r.reagent_id) & (bottles.status == IN_STOCK)
                        & (bottles.remaining_qty > 0)]
            total = self.total_stock(r.reagent_id, bottles)
            nearest = b.expiry_date.min() if len(b) else ""
            last_in = b.received_date.max() if len(b) else ""
            if total <= 0:
                flag = "缺貨"
            elif total < r.safety_stock:
                flag = "低庫存"
            else:
                flag = "正常"
            if any(parse_date(d) < self.today for d in b.expiry_date):
                flag += "・有過期瓶"
            rows.append({"reagent_id": r.reagent_id, "name": r["name"], "category": r.category,
                         "unit": r.unit, "location": r.location, "vendor": r.vendor,
                         "total": total, "safety_stock": float(r.safety_stock), "bottles": len(b),
                         "nearest_expiry": nearest, "last_received": last_in, "flag": flag})
        cols = ["reagent_id", "name", "category", "unit", "location", "vendor", "total",
                "safety_stock", "bottles", "nearest_expiry", "last_received", "flag"]
        return pd.DataFrame(rows, columns=cols)

    def low_stock(self) -> pd.DataFrame:
        s = self.summary()
        return s[s.total < s.safety_stock].reset_index(drop=True)

    def expiring(self, days: int = EXPIRY_WARNING_DAYS) -> pd.DataFrame:
        """在 days 天內到期（含已過期）且還有剩量的瓶。"""
        reagents, bottles, _ = self._tables()
        b = bottles[(bottles.status == IN_STOCK) & (bottles.remaining_qty > 0)].copy()
        if b.empty:
            return b.assign(days_left=[], name=[], unit=[])
        b["days_left"] = [(parse_date(d) - self.today).days for d in b.expiry_date]
        b = b[b.days_left <= days]
        active = reagents[reagents.active].set_index("reagent_id")
        b = b[b.reagent_id.isin(active.index)]
        b["name"] = b.reagent_id.map(active["name"])
        b["unit"] = b.reagent_id.map(active["unit"])
        return b.sort_values("days_left").reset_index(drop=True)

    def history(self, reagent_id: str | None = None, limit: int = 10) -> pd.DataFrame:
        log = self.storage.load("usage_log")
        if reagent_id:
            log = log[log.reagent_id == reagent_id]
        return log.tail(limit).iloc[::-1].reset_index(drop=True)

    def expiry_cutoff(self, days: int = EXPIRY_WARNING_DAYS) -> date:
        return self.today + timedelta(days=days)
