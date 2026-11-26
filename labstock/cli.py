"""介面層：文字選單。只負責「問使用者、顯示結果」，所有運算都呼叫其他模組的函式。"""
from __future__ import annotations

import argparse
import shutil
import sys
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd

from . import __version__
from .config import BASE_DIR, DEFAULT_DATA_DIR, EXPIRY_WARNING_DAYS, SAMPLE_DATA_DIR, load_email_settings
from .export import export_bottles, export_report
from .inventory import Inventory, InventoryError, parse_date
from .notifier import Notifier, daily_reminder, low_stock_message, send_alerts
from .search import SORT_KEYS, plot_stock, search, sort_summary, stats
from .storage import Storage

OUTPUT_DIR = BASE_DIR / "output"
LINE = "─" * 60


# ---------- 顯示工具 ----------
def ask(prompt: str) -> str:
    """讀取輸入；若輸入來自檔案（自動測試／示範），把輸入內容印出，紀錄才看得到。"""
    text = input(prompt)
    if not sys.stdin.isatty():
        print(text)
    return text.strip()


def width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def pad(s, w: int) -> str:
    s = str(s)
    return s + " " * max(0, w - width(s))


def table(df: pd.DataFrame, cols: dict[str, str]) -> str:
    """把 DataFrame 印成中英文對齊的表格；cols = {欄位: 標題}。"""
    if df.empty:
        return "  （沒有資料）"
    data = [[_fmt(v) for v in df[c]] for c in cols]
    ws = [max([width(h)] + [width(v) for v in col]) for h, col in zip(cols.values(), data)]
    out = ["  " + "  ".join(pad(h, w) for h, w in zip(cols.values(), ws)),
           "  " + "  ".join("-" * w for w in ws)]
    for row in zip(*data):
        out.append("  " + "  ".join(pad(v, w) for v, w in zip(row, ws)))
    return "\n".join(out)


def rel(p) -> str:
    """顯示相對於程式資料夾的路徑（截圖時不會露出電腦帳號名稱）。"""
    try:
        return str(Path(p).resolve().relative_to(BASE_DIR))
    except ValueError:
        return str(p)


def k_label(key: str) -> str:
    from .inventory import EDITABLE
    return EDITABLE.get(key, key)


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


SUMMARY_COLS = {"reagent_id": "編號", "name": "試劑名稱", "total": "可用量", "unit": "單位",
                "safety_stock": "安全量", "bottles": "包裝數", "nearest_expiry": "最近效期",
                "location": "存放位置", "flag": "狀態"}


# ---------- 各功能畫面 ----------
class App:
    def __init__(self, inv: Inventory, notifier: Notifier, user: str):
        self.inv, self.notifier, self.user = inv, notifier, user

    def pick_reagent(self) -> pd.Series | None:
        kw = ask("輸入試劑關鍵字（名稱／類別／位置，直接 Enter 列出全部）：")
        found = search(self.inv, kw)
        if found.empty:
            print("  查無符合的試劑。若是新試劑，請用選單 4「新增試劑品項」。")
            return None
        if len(found) == 1:
            r = found.iloc[0]
            print(f"  找到：{r.reagent_id} {r['name']}")
            return r
        print(table(found, SUMMARY_COLS))
        rid = ask("輸入編號（例如 R001）：").upper()
        hit = found[found.reagent_id == rid]
        if hit.empty:
            print("  編號不在上面的清單中。")
            return None
        return hit.iloc[0]

    def show_bottles(self, rid: str) -> pd.DataFrame:
        b = self.inv.bottles_of(rid)
        b = b.assign(no=["#" + str(n) for n in b.bottle_no],
                     note=["已過期！" if d < 0 else ("即將到期" if d <= EXPIRY_WARNING_DAYS else "")
                           for d in b.days_left])
        print(table(b, {"no": "序號", "remaining_qty": "剩量", "received_date": "入庫日",
                        "expiry_date": "效期", "days_left": "剩餘天數", "note": "備註"}))
        return b

    # 1 查詢
    def query(self):
        kw = ask("輸入關鍵字（直接 Enter 列出全部）：")
        found = search(self.inv, kw)
        print(f"\n  共 {len(found)} 項：")
        print(table(found, SUMMARY_COLS))
        if found.empty:
            return
        key = ask("排序方式 1名稱 2剩量 3最近效期 4最近入庫（Enter 跳過）：")
        if key in SORT_KEYS:
            print(f"\n  依「{SORT_KEYS[key][2]}」排序：")
            print(table(sort_summary(found, key), SUMMARY_COLS))
        rid = ask("輸入編號查看每個包裝的明細與紀錄（Enter 返回）：").upper()
        if rid in set(found.reagent_id):
            print("\n  【各包裝（依先進先出排序）】")
            self.show_bottles(rid)
            print("\n  【最近紀錄】")
            print(table(self.inv.history(rid, 5), {"time": "時間", "user": "操作者", "action": "動作",
                                                    "bottle_id": "包裝", "qty": "數量", "note": "備註"}))

    # 2 領用
    def use(self):
        r = self.pick_reagent()
        if r is None:
            return
        rid = r.reagent_id
        b = self.show_bottles(rid)
        if b.empty:
            print(f"  目前沒有在庫的{r.container}，請先入庫。")
            return
        pick = self.inv.suggest_bottle(rid)
        if pick is None:
            print(f"  所有在庫的{r.container}都已過期，請確認是否還能使用並處理過期試劑。")
            return
        print(f"\n  ▶ 建議使用 #{pick.bottle_no}（最早到期且未過期，效期 {pick.expiry_date}）")
        c = r.container
        choice = ask(f"使用量（{r.unit}；輸入 a＝此{c}整{c}用完；輸入 #序號 可改用其他{c}）：")
        bottle_id = pick.bottle_id
        if choice.startswith("#"):
            no = choice[1:]
            hit = b[b.bottle_no.astype(str) == no]
            if hit.empty:
                print("  沒有這個序號。")
                return
            bottle_id = hit.iloc[0].bottle_id
            choice = ask(f"使用量（{r.unit}；a＝整{c}用完）：")
        if choice.lower() == "a":
            res = self.inv.use_up(rid, bottle_id, self.user)
        else:
            res = self.inv.use(rid, choice, self.user, bottle_id)
        print(f"\n  ✔ 已登錄：{res.name} #{res.bottle_no} 使用 {res.used:g} {res.unit}，"
              f"此{c}剩 {res.bottle_remaining:g} {res.unit}")
        print(f"  ✔ 可用總量：{res.total_before:g} → {res.total_after:g} {res.unit}"
              f"（安全量 {res.safety_stock:g}）")
        if res.crossed_below_safety:
            print("  ⚠ 已低於安全庫存量，自動寄出補貨提醒……")
            low = self.inv.low_stock()
            subject, body = low_stock_message(low[low.reagent_id == rid], self.inv.today)
            print("  " + self._describe(self.notifier.send(subject, body)))

    # 3 入庫
    def stock_in(self):
        r = self.pick_reagent()
        if r is None:
            return
        c = r.container
        n = ask(f"這次分裝幾{c}：")
        q = ask(f"每{c}容量（{r.unit}）：")
        exp = ask("效期（YYYY-MM-DD）：")
        ids = self.inv.stock_in(r.reagent_id, n, q, exp, self.user)
        nos = [i.split("-")[1].lstrip("0") for i in ids]
        print(f"\n  ✔ 已入庫 {len(ids)} {c}「{r['name']}」，自動編號 #{nos[0]}～#{nos[-1]}，"
              f"請在每{c}上標上對應號碼。")
        print(f"  ✔ 可用總量：{self.inv.total_stock(r.reagent_id):g} {r.unit}")

    # 4 新增
    def add(self):
        name = ask("試劑名稱：")
        unit = ask("單位（mL、g、支、管…）：")
        container = ask("包裝單位（瓶／管／盒／包／罐…，直接 Enter＝瓶）：") or "瓶"
        safety = ask("安全庫存量（低於此數量就提醒）：")
        loc = ask("存放位置（例如 -20°C 冰箱 A 層）：")
        cat = ask("類別（可空白）：")
        vendor = ask("廠商（可空白）：")
        rid = self.inv.add_reagent(name, unit, safety, loc, cat, vendor, self.user, container)
        print(f"  ✔ 已新增 {rid}「{name}」。")
        if ask("要現在入庫嗎？(y/n)：").lower() == "y":
            n = ask(f"這次分裝幾{container}：")
            q = ask(f"每{container}容量（{unit}）：")
            exp = ask("效期（YYYY-MM-DD）：")
            ids = self.inv.stock_in(rid, n, q, exp, self.user)
            print(f"  ✔ 已入庫 {len(ids)} {container}，編號 #1～#{len(ids)}。")

    # 5 刪除
    def remove(self):
        r = self.pick_reagent()
        if r is None:
            return
        if ask(f"確定刪除「{r['name']}」？刪除後不再顯示，但保留歷史紀錄 (y/n)：").lower() == "y":
            self.inv.deactivate_reagent(r.reagent_id, self.user)
            print("  ✔ 已刪除。")
        else:
            print("  已取消。")

    # 9 撤銷領用
    def undo(self):
        recent = self.inv.recent_uses(10)
        print("\n  【最近的領用紀錄】（最新在上；已撤銷的不顯示）")
        r = recent.assign(no=["#" + str(int(n)) if str(n) != "nan" else "" for n in recent.bottle_no])
        print(table(r, {"log_id": "紀錄編號", "time": "時間", "user": "操作者", "name": "試劑名稱",
                        "no": "序號", "qty": "用量", "unit": "單位"}))
        if recent.empty:
            return
        log_id = ask("輸入要撤銷的紀錄編號（例如 L00012，Enter 取消）：").upper()
        if not log_id:
            print("  已取消。")
            return
        if ask(f"確定撤銷 {log_id}？用量會加回原本的包裝 (y/n)：").lower() != "y":
            print("  已取消。")
            return
        res = self.inv.undo_use(log_id, self.user)
        print(f"  ✔ 已撤銷 {res.log_id}：{res.name} #{res.bottle_no} 加回 {res.qty:g} {res.unit}，"
              f"現在剩 {res.bottle_remaining:g} {res.unit}；可用總量 {res.total_after:g} {res.unit}")

    # 10 編輯品項
    def edit(self):
        r = self.pick_reagent()
        if r is None:
            return
        print("  每一項直接 Enter＝不修改。")
        changes = {}
        for key, label in [("name", "名稱"), ("safety_stock", "安全庫存量"), ("location", "存放位置"),
                           ("container", "包裝單位"), ("unit", "單位"), ("category", "類別"), ("vendor", "廠商")]:
            cur = f"{r[key]:g}" if key == "safety_stock" else r[key]
            changes[key] = ask(f"{label}［目前：{cur or '（空白）'}］：")
        diff = self.inv.edit_reagent(r.reagent_id, self.user, **changes)
        for k, (a, b) in diff.items():
            print(f"  ✔ {k_label(k)}：{_fmt(a)} → {_fmt(b)}")

    # 6 提醒清單
    def alerts(self, interactive: bool = True):
        low, exp = self.inv.low_stock(), self.inv.expiring()
        print(f"\n  【低於安全庫存】{len(low)} 項")
        print(table(low, {"reagent_id": "編號", "name": "試劑名稱", "total": "可用量", "unit": "單位",
                          "safety_stock": "安全量", "location": "存放位置", "flag": "狀態"}))
        print(f"\n  【{EXPIRY_WARNING_DAYS} 天內到期或已過期】{len(exp)} 件")
        e = exp.assign(no=["#" + str(n) for n in exp.bottle_no],
                       note=["已過期" if d < 0 else "" for d in exp.days_left]) if len(exp) else exp
        print(table(e, {"name": "試劑名稱", "no": "序號", "remaining_qty": "剩量", "unit": "單位",
                        "expiry_date": "效期", "days_left": "剩餘天數", "note": "備註"}))
        if interactive and (len(low) or len(exp)) and ask("\n要寄出提醒信嗎？(y/n)：").lower() == "y":
            self.send_alerts(low, exp)

    def send_alerts(self, low=None, exp=None):
        for label, result in send_alerts(self.inv, self.notifier):
            print(f"  {label}：" + self._describe(result))

    # 7 統計
    def report_stats(self):
        st = stats(self.inv)
        for k, v in st.items():
            if isinstance(v, dict):
                v = "、".join(f"{c or '未分類'} {n}" for c, n in v.items())
            print(f"  {k}：{v}")
        p = plot_stock(self.inv, OUTPUT_DIR / f"庫存圖表_{self.inv.today:%Y%m%d}.png")
        print(f"  ✔ 庫存圖表已存到 {rel(p)}")

    # 8 匯出
    def export(self):
        p1 = export_report(self.inv, OUTPUT_DIR / f"庫存報表_{self.inv.today:%Y%m%d}.csv")
        p2 = export_bottles(self.inv, OUTPUT_DIR / f"包裝明細_{self.inv.today:%Y%m%d}.csv")
        print(f"  ✔ 已匯出：{rel(p1)}\n  ✔ 已匯出：{rel(p2)}\n  （可直接用 Excel 開啟）")

    @staticmethod
    def _describe(result: str) -> str:
        if result == "sent":
            return "✔ 已寄出電子郵件"
        if result.startswith("dry-run"):
            return f"（測試模式，未設定寄件帳號）信件內容已存到 {rel(result.split(': ', 1)[1])}"
        return f"✘ 寄送失敗 {result.split(': ', 1)[1]}，請檢查網路或 config.ini"

    def daily_check(self, state_file: Path):
        """每天第一次開啟程式時，若有低庫存或到期品項，自動寄一封每日摘要。"""
        low, exp = self.inv.low_stock(), self.inv.expiring()
        if not (len(low) or len(exp)):
            return
        print(f"  ⚠ 提醒：{len(low)} 項低於安全庫存、{len(exp)} 件 {EXPIRY_WARNING_DAYS} 天內到期或已過期（選單 6 查看）")
        results = daily_reminder(self.inv, self.notifier, state_file)
        if results:
            print("  今日第一次開啟，自動寄出每日提醒：")
            for label, result in results:
                print(f"  {label}：" + self._describe(result))

MENU = """
{line}
 LabStock 實驗室試劑庫存管理    操作者：{user}    日期：{today}
{line}
 1. 查詢／搜尋試劑            6. 低庫存與到期清單／寄提醒
 2. 領用試劑（扣量）          7. 庫存統計與圖表
 3. 入庫（新貨分裝）          8. 匯出庫存報表（CSV）
 4. 新增試劑品項              9. 撤銷領用紀錄
 5. 刪除試劑品項             10. 編輯試劑品項
 0. 離開
{line}"""


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="LabStock 實驗室試劑庫存管理")
    ap.add_argument("--data", default=str(DEFAULT_DATA_DIR), help="資料夾位置（預設 data/lab）")
    ap.add_argument("--load-sample", action="store_true", help="把範例資料複製到資料夾（會覆蓋）")
    ap.add_argument("--today", help="指定今天日期 YYYY-MM-DD（示範與測試用）")
    args = ap.parse_args(argv)

    data_dir = Path(args.data)
    if args.load_sample:
        data_dir.mkdir(parents=True, exist_ok=True)
        for f in SAMPLE_DATA_DIR.glob("*.csv"):
            shutil.copy(f, data_dir / f.name)
        (data_dir / "notify_state.txt").unlink(missing_ok=True)
        print(f"已載入範例資料到 {data_dir}")

    today = parse_date(args.today) if args.today else date.today()
    inv = Inventory(Storage(data_dir), today)
    notifier = Notifier(load_email_settings(), data_dir / "outbox")

    for stream in (sys.stdout, sys.stderr):  # Windows 主控台遇到無法顯示的符號時以 ? 取代，不會當掉
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    print(f"LabStock v{__version__}")
    user = ask("請輸入你的名字（記錄操作者用）：") or "未填"
    app = App(inv, notifier, user)
    app.daily_check(data_dir / "notify_state.txt")

    actions = {"1": app.query, "2": app.use, "3": app.stock_in, "4": app.add, "5": app.remove,
               "6": app.alerts, "7": app.report_stats, "8": app.export, "9": app.undo, "10": app.edit}
    while True:
        print(MENU.format(line=LINE, user=user, today=today))
        try:
            choice = ask("請選擇功能：")
        except EOFError:
            break
        if choice == "0":
            print("再見！")
            break
        fn = actions.get(choice)
        if fn is None:
            print("  請輸入 0～10。")
            continue
        try:
            fn()
        except InventoryError as e:
            print(f"  ✘ {e}")
        except EOFError:
            break
