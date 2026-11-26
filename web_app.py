"""LabStock 網頁版（本地網頁）。

啟動：streamlit run web_app.py
      同一網路的其他電腦可用瀏覽器開啟 http://<這台電腦的 IP>:8501
示範：streamlit run web_app.py -- --data data/demo --load-sample --today 2026-11-27

網頁版只是另一個「介面層」：所有計算都呼叫 labstock 套件裡的同一組函式，
所以文字選單與網頁看到的是同一份資料、同一套規則。
"""
from __future__ import annotations

import argparse
import io
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from labstock import __version__
from labstock.config import DEFAULT_DATA_DIR, EXPIRY_WARNING_DAYS, SAMPLE_DATA_DIR, load_email_settings
from labstock.export import HEADERS
from labstock.inventory import CONTAINERS, Inventory, InventoryError, parse_date
from labstock.notifier import Notifier, daily_reminder, low_stock_message, send_alerts
from labstock.search import SORT_KEYS, search, sort_summary, stats, stock_figure
from labstock.storage import Storage

st.set_page_config(page_title="LabStock 實驗室試劑庫存", page_icon="🧪", layout="wide")


# ---------- 啟動參數與共用物件 ----------
@st.cache_resource
def setup(argv: tuple[str, ...]):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--load-sample", action="store_true")
    ap.add_argument("--today")
    args, _ = ap.parse_known_args(list(argv))
    data_dir = Path(args.data)
    if args.load_sample:
        data_dir.mkdir(parents=True, exist_ok=True)
        for f in SAMPLE_DATA_DIR.glob("*.csv"):
            shutil.copy(f, data_dir / f.name)
        (data_dir / "notify_state.txt").unlink(missing_ok=True)
    return data_dir, (parse_date(args.today) if args.today else None)


DATA_DIR, FIXED_TODAY = setup(tuple(sys.argv[1:]))
inv = Inventory(Storage(DATA_DIR), FIXED_TODAY or date.today())
notifier = Notifier(load_email_settings(), DATA_DIR / "outbox")

SUMMARY_COLS = {"reagent_id": "編號", "name": "試劑名稱", "total": "可用量", "unit": "單位",
                "safety_stock": "安全量", "bottles": "包裝數", "container": "包裝單位",
                "nearest_expiry": "最近效期", "location": "存放位置", "flag": "狀態"}


def show_table(df: pd.DataFrame, cols: dict[str, str], flag_col: str | None = None):
    if df.empty:
        st.info("沒有資料")
        return
    view = df[list(cols)].rename(columns=cols)

    def color(row):
        text = str(row.get(cols.get(flag_col, ""), "")) if flag_col else ""
        if "低庫存" in text or "缺貨" in text or "已過期" in text:
            return ["background-color: #FCE8E6; color: #A50E0E"] * len(row)
        if "有過期" in text or "即將到期" in text:
            return ["background-color: #FEF7E0; color: #7A4F01"] * len(row)
        return [""] * len(row)

    fmt = {c: (lambda v: f"{v:g}") for c in view.columns if pd.api.types.is_float_dtype(view[c])}
    st.dataframe(view.style.apply(color, axis=1).format(fmt),
                 hide_index=True, use_container_width=True)


def describe(result: str) -> str:
    if result == "sent":
        return "已寄出電子郵件"
    if result.startswith("dry-run"):
        return "測試模式（尚未設定寄件帳號），信件內容已存到 outbox/"
    return "寄送失敗，信件已存到 outbox/，請檢查網路或 config.ini"


def reagent_picker(label: str, key: str) -> pd.Series | None:
    s = inv.summary()
    if s.empty:
        st.warning("目前沒有任何試劑，請先到「品項管理」新增。")
        return None
    options = {f"{r.reagent_id}｜{r['name']}（{r.location or '未填位置'}）": r.reagent_id for _, r in s.iterrows()}
    choice = st.selectbox(label, list(options), key=key, help="可直接打字搜尋")
    return s[s.reagent_id == options[choice]].iloc[0]


def flash(kind: str, msg: str) -> None:
    """操作成功後先記下訊息、重新整理頁面，讓表格與數字顯示最新狀態，再把訊息顯示出來。"""
    st.session_state.setdefault("flash", []).append((kind, msg))


def show_flash() -> None:
    for kind, msg in st.session_state.pop("flash", []):
        getattr(st, kind)(msg)


def need_user() -> bool:
    if not st.session_state.get("user"):
        st.warning("請先在左側輸入你的名字（記錄是誰操作）。")
        return True
    return False


# ---------- 側邊欄 ----------
with st.sidebar:
    st.title("🧪 LabStock")
    st.caption(f"實驗室試劑庫存管理 v{__version__}")
    st.text_input("操作者名字", key="user", placeholder="例如：小林")
    page = st.radio("功能", ["總覽", "查詢", "領用", "撤銷領用", "入庫", "品項管理", "提醒清單", "統計與匯出"])
    st.divider()
    st.caption(f"今天：{inv.today}")
    st.caption(f"資料夾：{DATA_DIR.name}/")

# 每天第一次開啟時自動寄每日提醒（整個伺服器每天一次）
if "daily_checked" not in st.session_state:
    st.session_state.daily_checked = True
    results = daily_reminder(inv, notifier, DATA_DIR / "notify_state.txt")
    if results:
        st.toast("今日第一次開啟，已自動寄出每日提醒：" + "、".join(f"{a}（{describe(b)}）" for a, b in results))

user = st.session_state.get("user", "").strip()
low, exp = inv.low_stock(), inv.expiring()
show_flash()

# ---------- 各頁面 ----------
if page == "總覽":
    st.header("庫存總覽")
    if len(low) or len(exp):
        st.error(f"⚠ {len(low)} 項低於安全庫存、{len(exp)} 件 {EXPIRY_WARNING_DAYS} 天內到期或已過期，請到「提醒清單」查看。")
    stt = stats(inv)
    c = st.columns(5)
    c[0].metric("品項數", stt["品項數"])
    c[1].metric("在庫包裝數", stt["在庫包裝數"])
    c[2].metric("低庫存", stt["低庫存品項"])
    c[3].metric("缺貨", stt["缺貨品項"])
    c[4].metric("30 天內到期", stt["30 天內到期（含已過期）件數"])
    left, right = st.columns([1, 1])
    with left:
        st.subheader("各試劑庫存狀態")
        st.pyplot(stock_figure(inv), use_container_width=True)
    with right:
        st.subheader("需要注意")
        show_table(low, {"name": "試劑名稱", "total": "可用量", "unit": "單位", "safety_stock": "安全量",
                         "flag": "狀態"}, "flag")
        if len(exp):
            e = exp.assign(note=["已過期" if d < 0 else "即將到期" for d in exp.days_left],
                           no=["#" + str(int(n)) for n in exp.bottle_no])
            show_table(e, {"name": "試劑名稱", "no": "序號", "expiry_date": "效期", "days_left": "剩餘天數",
                           "note": "備註"}, "note")

elif page == "查詢":
    st.header("查詢／搜尋試劑")
    a, b = st.columns([3, 2])
    kw = a.text_input("關鍵字（名稱、類別、存放位置、廠商；空白＝全部）", placeholder="例如：taq、冰箱、耗材")
    sort_key = b.selectbox("排序", list(SORT_KEYS), format_func=lambda k: SORT_KEYS[k][2])
    found = sort_summary(search(inv, kw), sort_key)
    st.caption(f"共 {len(found)} 項")
    show_table(found, SUMMARY_COLS, "flag")
    if not found.empty:
        rid = st.selectbox("查看明細", found.reagent_id, format_func=lambda r: f"{r}｜{found.set_index('reagent_id').loc[r, 'name']}")
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("各包裝（依先進先出排序）")
            bt = inv.bottles_of(rid)
            bt = bt.assign(no=["#" + str(n) for n in bt.bottle_no],
                           note=["已過期" if d < 0 else ("即將到期" if d <= EXPIRY_WARNING_DAYS else "") for d in bt.days_left])
            show_table(bt, {"no": "序號", "remaining_qty": "剩量", "received_date": "入庫日",
                            "expiry_date": "效期", "days_left": "剩餘天數", "note": "備註"}, "note")
        with c2:
            st.subheader("最近紀錄")
            show_table(inv.history(rid, 8), {"time": "時間", "user": "操作者", "action": "動作",
                                              "qty": "數量", "note": "備註"})

elif page == "領用":
    st.header("領用試劑（扣量）")
    r = reagent_picker("要領用的試劑", "use_pick")
    if r is not None:
        c = r.container
        bt = inv.bottles_of(r.reagent_id)
        pick = inv.suggest_bottle(r.reagent_id)
        if bt.empty:
            st.warning(f"目前沒有在庫的{c}，請先入庫。")
        elif pick is None:
            st.error(f"所有在庫的{c}都已過期，請確認是否還能使用並處理過期試劑。")
        else:
            st.info(f"▶ 建議使用 **#{pick.bottle_no}**（最早到期且未過期，效期 {pick.expiry_date}，剩 {pick.remaining_qty:g} {r.unit}）")
            usable = bt[bt.days_left >= 0]
            with st.form("use_form"):
                no = st.selectbox(f"使用哪一{c}", list(usable.bottle_no),
                                  format_func=lambda n: f"#{n}（剩 {usable.set_index('bottle_no').loc[n, 'remaining_qty']:g} {r.unit}，效期 {usable.set_index('bottle_no').loc[n, 'expiry_date']}）")
                qty = st.number_input(f"使用量（{r.unit}）", min_value=0.0, step=1.0, format="%g")
                all_used = st.checkbox(f"這{c}已整{c}用完（忽略上面的使用量）")
                ok = st.form_submit_button("登錄領用", type="primary")
            if ok and not need_user():
                bid = usable[usable.bottle_no == no].iloc[0].bottle_id
                try:
                    res = inv.use_up(r.reagent_id, bid, user) if all_used else inv.use(r.reagent_id, qty, user, bid)
                    flash("success", f"已登錄：{res.name} #{res.bottle_no} 使用 {res.used:g} {res.unit}，此{c}剩 "
                          f"{res.bottle_remaining:g} {res.unit}；可用總量 {res.total_before:g} → {res.total_after:g} "
                          f"{res.unit}（安全量 {res.safety_stock:g}）")
                    if res.crossed_below_safety:
                        l2 = inv.low_stock()
                        result = notifier.send(*low_stock_message(l2[l2.reagent_id == r.reagent_id], inv.today))
                        flash("warning", f"⚠ 已低於安全庫存量，自動寄出補貨提醒：{describe(result)}")
                    st.rerun()
                except InventoryError as e:
                    st.error(str(e))

elif page == "撤銷領用":
    st.header("撤銷領用紀錄")
    st.caption("登錄錯誤時使用：用量會加回原本的包裝，並留下一筆「撤銷領用」紀錄。")
    only_me = st.checkbox("只看我的紀錄", value=False)
    recent = inv.recent_uses(15, user if only_me and user else None)
    recent = recent.assign(no=["#" + str(int(n)) for n in recent.bottle_no]) if len(recent) else recent
    show_table(recent, {"log_id": "紀錄編號", "time": "時間", "user": "操作者", "name": "試劑名稱",
                        "no": "序號", "qty": "用量", "unit": "單位"})
    if len(recent):
        with st.form("undo_form"):
            lid = st.selectbox("要撤銷的紀錄", recent.log_id, format_func=lambda l: (
                lambda x: f"{l}｜{x['time']} {x['user']} {x['name']} {x['no']} 用 {x['qty']:g} {x['unit']}")(
                recent.set_index('log_id').loc[l]))
            sure = st.checkbox("我確定要撤銷這筆紀錄")
            ok = st.form_submit_button("撤銷", type="primary")
        if ok and not need_user():
            if not sure:
                st.warning("請先勾選確認。")
            else:
                try:
                    res = inv.undo_use(lid, user)
                    flash("success", f"已撤銷 {res.log_id}：{res.name} #{res.bottle_no} 加回 {res.qty:g} {res.unit}，"
                          f"現在剩 {res.bottle_remaining:g}；可用總量 {res.total_after:g} {res.unit}")
                    st.rerun()
                except InventoryError as e:
                    st.error(str(e))

elif page == "入庫":
    st.header("入庫（新貨分裝）")
    r = reagent_picker("入庫的試劑", "in_pick")
    if r is not None:
        c = r.container
        with st.form("in_form"):
            a, b, d = st.columns(3)
            n = a.number_input(f"這次分裝幾{c}", min_value=1, max_value=200, value=1, step=1)
            q = b.number_input(f"每{c}容量（{r.unit}）", min_value=0.0, step=1.0, format="%g")
            exp_d = d.date_input("效期", value=inv.today + timedelta(days=365))
            ok = st.form_submit_button("確認入庫", type="primary")
        if ok and not need_user():
            try:
                ids = inv.stock_in(r.reagent_id, n, q, exp_d.isoformat(), user)
                nos = [i.split("-")[1].lstrip("0") for i in ids]
                flash("success", f"已入庫 {len(ids)} {c}「{r['name']}」，自動編號 #{nos[0]}～#{nos[-1]}，"
                      f"請在每{c}上標上對應號碼。可用總量：{inv.total_stock(r.reagent_id):g} {r.unit}")
                st.rerun()
            except InventoryError as e:
                st.error(str(e))

elif page == "品項管理":
    st.header("品項管理")
    mode = st.radio("動作", ["新增品項", "編輯品項", "刪除品項"], horizontal=True, key="item_mode",
                    label_visibility="collapsed")
    if mode == "新增品項":
        with st.form("add_form"):
            a, b, c3 = st.columns(3)
            name = a.text_input("試劑名稱 *")
            unit = b.text_input("單位 *", placeholder="mL、g、支、μL")
            container = c3.selectbox("包裝單位", CONTAINERS)
            a, b, c3 = st.columns(3)
            safety = a.number_input("安全庫存量 *（低於此數量就提醒）", min_value=0.0, step=1.0, format="%g")
            loc = b.text_input("存放位置", placeholder="-20°C 冰箱 A 層")
            cat = c3.text_input("類別", placeholder="酵素、耗材…")
            vendor = st.text_input("廠商")
            ok = st.form_submit_button("新增", type="primary")
        if ok and not need_user():
            try:
                rid = inv.add_reagent(name, unit, safety, loc, cat, vendor, user, container)
                flash("success", f"已新增 {rid}「{name}」，可到「入庫」登錄第一批。")
                st.rerun()
            except InventoryError as e:
                st.error(str(e))
    if mode == "編輯品項":
        r = reagent_picker("要編輯的試劑", "edit_pick")
        if r is not None:
            with st.form("edit_form"):
                a, b, c3 = st.columns(3)
                v_name = a.text_input("名稱", r["name"])
                v_safety = b.number_input("安全庫存量", min_value=0.0, value=float(r.safety_stock), step=1.0, format="%g")
                v_loc = c3.text_input("存放位置", r.location)
                a, b, c3 = st.columns(3)
                v_cont = a.selectbox("包裝單位", CONTAINERS, index=CONTAINERS.index(r.container) if r.container in CONTAINERS else 0)
                v_unit = b.text_input("單位（有庫存時不能改）", r.unit)
                v_cat = c3.text_input("類別", r.category)
                v_vendor = st.text_input("廠商", r.vendor)
                ok = st.form_submit_button("儲存修改", type="primary")
            if ok and not need_user():
                try:
                    diff = inv.edit_reagent(r.reagent_id, user, name=v_name, safety_stock=v_safety, location=v_loc,
                                            container=v_cont, unit=v_unit, category=v_cat, vendor=v_vendor)
                    from labstock.inventory import EDITABLE
                    flash("success", "已修改：" + "；".join(
                        f"{EDITABLE[k]} {a:g} → {b:g}" if k == "safety_stock" else f"{EDITABLE[k]} {a} → {b}"
                        for k, (a, b) in diff.items()))
                    st.rerun()
                except InventoryError as e:
                    st.error(str(e))
    if mode == "刪除品項":
        r = reagent_picker("要刪除的試劑", "del_pick")
        if r is not None:
            st.caption("刪除＝停用：清單不再顯示，但保留所有歷史紀錄。")
            sure = st.checkbox(f"我確定要刪除「{r['name']}」")
            if st.button("刪除", type="primary", disabled=not sure) and not need_user():
                inv.deactivate_reagent(r.reagent_id, user)
                flash("success", f"已刪除「{r['name']}」（歷史紀錄保留）。")
                st.rerun()

elif page == "提醒清單":
    st.header("低庫存與到期清單")
    st.subheader(f"低於安全庫存（{len(low)} 項）")
    show_table(low, {"reagent_id": "編號", "name": "試劑名稱", "total": "可用量", "unit": "單位",
                     "safety_stock": "安全量", "location": "存放位置", "flag": "狀態"}, "flag")
    st.subheader(f"{EXPIRY_WARNING_DAYS} 天內到期或已過期（{len(exp)} 件）")
    if len(exp):
        e = exp.assign(no=["#" + str(int(n)) for n in exp.bottle_no],
                       note=["已過期" if d < 0 else "即將到期" for d in exp.days_left])
        show_table(e, {"name": "試劑名稱", "no": "序號", "remaining_qty": "剩量", "unit": "單位",
                       "expiry_date": "效期", "days_left": "剩餘天數", "note": "備註"}, "note")
    else:
        st.info("沒有資料")
    if (len(low) or len(exp)) and st.button("寄出提醒信給負責採購者", type="primary"):
        for label, result in send_alerts(inv, notifier):
            flash("success", f"{label}：{describe(result)}")
        st.rerun()

elif page == "統計與匯出":
    st.header("統計與匯出")
    stt = stats(inv)
    st.write("、".join(f"{k}：{v}" for k, v in stt.items() if not isinstance(v, dict)))
    st.write("各類別品項數：" + "、".join(f"{c or '未分類'} {n}" for c, n in stt["各類別品項數"].items()))
    fig = stock_figure(inv)
    st.pyplot(fig, use_container_width=True)
    png = io.BytesIO()
    fig.savefig(png, dpi=150)
    report = inv.summary().rename(columns=HEADERS).to_csv(index=False).encode("utf-8-sig")
    a, b = st.columns(2)
    a.download_button("下載庫存報表（CSV，Excel 可開）", report, f"庫存報表_{inv.today:%Y%m%d}.csv", "text/csv")
    b.download_button("下載庫存圖表（PNG）", png.getvalue(), f"庫存圖表_{inv.today:%Y%m%d}.png", "image/png")
