"""LabStock 測試案例。函式名稱前的 TCxx 對應 HW5 報告第 5 節的測試案例表。"""
import smtplib

import pandas as pd
import pytest

from labstock.config import EmailSettings, load_email_settings
from labstock.export import export_report
from labstock.inventory import Inventory, InventoryError
from labstock.notifier import Notifier, low_stock_message
from labstock.search import plot_stock, search, sort_summary
from labstock.storage import Storage


# ---------- F1 試劑資料管理 ----------
def test_TC01_新增試劑後可查到(inv):
    rid = inv.add_reagent("10X PBS", "mL", 200, "室溫櫃")
    s = inv.summary()
    assert rid == "R001"
    assert s.loc[0, "name"] == "10X PBS" and s.loc[0, "flag"] == "缺貨"


def test_TC02_重複名稱或空白名稱會被拒絕(inv):
    inv.add_reagent("10X PBS", "mL", 200)
    with pytest.raises(InventoryError, match="已經有"):
        inv.add_reagent("10x pbs", "mL", 100)   # 不分大小寫
    with pytest.raises(InventoryError, match="不可空白"):
        inv.add_reagent("  ", "mL", 100)
    with pytest.raises(InventoryError, match="不可小於 0"):
        inv.add_reagent("Tris", "g", -5)


def test_TC03_刪除品項後不顯示但保留紀錄(inv, taq):
    inv.deactivate_reagent(taq, user="測試")
    assert inv.summary().empty
    assert "刪除品項" in set(inv.history(taq).action)


# ---------- F2 入庫與瓶號 ----------
def test_TC04_入庫自動編號且第二批接續(inv, taq):
    ids = inv.stock_in(taq, 2, 50, "2027-12-31", user="測試")
    assert ids == ["R001-004", "R001-005"]
    assert inv.total_stock(taq) == 250


def test_TC05_入庫輸入錯誤會被拒絕(inv, taq):
    with pytest.raises(InventoryError, match="至少為 1"):
        inv.stock_in(taq, 0, 50, "2027-12-31")
    with pytest.raises(InventoryError, match="日期格式"):
        inv.stock_in(taq, 1, 50, "2027/12/31")
    with pytest.raises(InventoryError, match="效期早於"):
        inv.stock_in(taq, 1, 50, "2020-01-01")


# ---------- F3 領用扣量 ----------
def test_TC06_領用後剩量正確並寫入紀錄(inv, taq):
    res = inv.use(taq, 20, user="小林")
    assert (res.bottle_no, res.bottle_remaining, res.total_after) == (1, 30, 130)
    last = inv.history(taq, 1).iloc[0]
    assert (last.user, last.action, last.qty) == ("小林", "領用", 20)


def test_TC07_用量超過剩量或不是正數會被拒絕且資料不變(inv, taq):
    with pytest.raises(InventoryError, match="大於此瓶剩量"):
        inv.use(taq, 60)
    with pytest.raises(InventoryError, match="大於 0"):
        inv.use(taq, 0)
    with pytest.raises(InventoryError, match="必須是數字"):
        inv.use(taq, "abc")
    assert inv.total_stock(taq) == 150


def test_TC08_整瓶用完標記為用完並換下一瓶(inv, taq):
    inv.use_up(taq, "R001-001")
    b = inv.bottles_of(taq, include_empty=True)
    assert b.loc[b.bottle_id == "R001-001", "status"].item() == "用完"
    assert inv.suggest_bottle(taq).bottle_id == "R001-002"


# ---------- F6 先進先出 ----------
def test_TC09_先進先出建議最早到期且跳過已過期(inv, taq):
    inv.stock_in(taq, 1, 50, "2026-12-31", received_date="2026-11-20")   # 較早到期 → #4
    inv.stock_in(taq, 1, 50, "2026-11-25", received_date="2026-01-01")   # 已過期 → #5
    assert inv.suggest_bottle(taq).bottle_no == 4
    assert inv.total_stock(taq) == 200   # 過期的 #5 不算可用量


# ---------- F4 低庫存與到期提醒 ----------
def test_TC10_跌破安全量時才觸發提醒且只觸發一次(inv, taq):
    r1 = inv.use(taq, 40)            # 150 → 110，仍高於 100
    r2 = inv.use(taq, 10)            # 110 → 100，等於安全量不提醒
    r3 = inv.use(taq, 5, bottle_id="R001-002")   # 100 → 95，剛跌破 → 提醒
    r4 = inv.use(taq, 5, bottle_id="R001-002")   # 95 → 90，已經在下方 → 不重複提醒
    assert [r.crossed_below_safety for r in (r1, r2, r3, r4)] == [False, False, True, False]
    assert list(inv.low_stock().reagent_id) == [taq]


def test_TC11_到期清單包含30天內與已過期(inv, taq):
    inv.stock_in(taq, 1, 50, "2026-12-20", received_date="2026-11-01")   # 23 天後
    inv.stock_in(taq, 1, 50, "2026-11-20", received_date="2026-01-01")   # 已過期 7 天
    e = inv.expiring(30)
    assert list(e.days_left) == [-7, 23]
    assert "R001-001" not in set(e.bottle_id)    # 2027 年到期的不在清單


def test_TC12_未設定帳號時以測試模式存到outbox(tmp_path, inv, taq):
    n = Notifier(EmailSettings(enabled=False), tmp_path / "outbox")
    result = n.send(*low_stock_message(inv.summary(), inv.today))
    assert result.startswith("dry-run")
    saved = list((tmp_path / "outbox").glob("*.txt"))[0].read_text(encoding="utf-8")
    assert "Taq DNA Polymerase" in saved and "低於安全庫存" in saved


def test_TC13_寄信失敗時不中斷並改存outbox(tmp_path, monkeypatch):
    def broken(*a, **k):
        raise smtplib.SMTPAuthenticationError(535, b"bad password")
    monkeypatch.setattr(smtplib, "SMTP", broken)
    s = EmailSettings(enabled=True, sender="lab@example.com", password="x", recipients=["a@example.com"])
    result = Notifier(s, tmp_path / "outbox").send("主旨", "內容")
    assert result.startswith("failed: SMTPAuthenticationError")
    assert len(list((tmp_path / "outbox").glob("*.txt"))) == 1


def test_TC14_設定檔讀取(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text("[email]\nenabled = true\nsender = lab@example.com\napp_password = abcd\n"
                   "recipients = a@example.com, b@example.com\n", encoding="utf-8")
    s = load_email_settings(ini)
    assert s.enabled and s.recipients == ["a@example.com", "b@example.com"]
    assert load_email_settings(tmp_path / "不存在.ini").enabled is False


# ---------- F5 搜尋、排序、統計、圖表 ----------
def test_TC15_關鍵字搜尋不分大小寫並可搜位置(inv, taq):
    inv.add_reagent("10X PBS", "mL", 200, "室溫櫃", "緩衝液")
    assert list(search(inv, "taq").name) == ["Taq DNA Polymerase"]
    assert list(search(inv, "冰箱").name) == ["Taq DNA Polymerase"]
    assert len(search(inv, "")) == 2
    assert search(inv, "不存在的東西").empty


def test_TC16_依剩量排序(inv, taq):
    rid = inv.add_reagent("10X PBS", "mL", 200)
    inv.stock_in(rid, 1, 500, "2028-01-01")
    assert list(sort_summary(inv.summary(), "2").name) == ["Taq DNA Polymerase", "10X PBS"]


def test_TC17_產生庫存圖表(tmp_path, inv, taq):
    p = plot_stock(inv, tmp_path / "chart.png")
    assert p.exists() and p.stat().st_size > 5000


# ---------- 資料保存與匯出 ----------
def test_TC18_資料存檔後重新開啟仍在且有備份(tmp_path, inv, taq):
    inv.use(taq, 10)
    again = Inventory(Storage(tmp_path / "data"), today=inv.today)
    assert again.total_stock(taq) == 140
    assert any((tmp_path / "data" / "backup").glob("bottles_*.csv"))


def test_TC19_匯出中文欄位CSV(tmp_path, inv, taq):
    p = export_report(inv, tmp_path / "報表.csv")
    df = pd.read_csv(p, encoding="utf-8-sig")
    assert list(df.columns[:3]) == ["編號", "試劑名稱", "類別"]
    assert p.read_bytes().startswith(b"\xef\xbb\xbf")   # 有 BOM，Excel 開啟不亂碼
