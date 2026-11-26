"""搜尋與統計模組（F5）：關鍵字搜尋、排序、統計、庫存圖表。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .inventory import Inventory

SORT_KEYS = {
    "1": ("name", True, "名稱"),
    "2": ("total", True, "剩量（少→多）"),
    "3": ("nearest_expiry", True, "最近效期（早→晚）"),
    "4": ("last_received", False, "最近入庫（新→舊）"),
}


def search(inv: Inventory, keyword: str) -> pd.DataFrame:
    """在名稱、類別、存放位置、廠商中搜尋（不分大小寫）；空白關鍵字回傳全部。"""
    s = inv.summary()
    kw = keyword.strip().lower()
    if not kw:
        return s
    hay = (s["name"] + " " + s.category + " " + s.location + " " + s.vendor).str.lower()
    return s[hay.str.contains(kw, regex=False)].reset_index(drop=True)


def sort_summary(df: pd.DataFrame, key: str) -> pd.DataFrame:
    col, asc, _ = SORT_KEYS.get(key, SORT_KEYS["1"])
    # 沒有效期／入庫日（空字串）的品項排最後
    blank = df[col].astype(str) == ""
    return pd.concat([df[~blank].sort_values(col, ascending=asc), df[blank]]).reset_index(drop=True)


def stats(inv: Inventory) -> dict:
    s = inv.summary()
    return {
        "品項數": len(s),
        "在庫瓶數": int(s.bottles.sum()) if len(s) else 0,
        "低庫存品項": int((s.flag == "低庫存").sum()),
        "缺貨品項": int((s.flag == "缺貨").sum()),
        "30 天內到期（含已過期）瓶數": len(inv.expiring()),
        "各類別品項數": s.groupby("category").size().to_dict() if len(s) else {},
    }


def plot_stock(inv: Inventory, out_path: Path | str) -> Path:
    """畫出各試劑「可用量 ÷ 安全庫存量」的長條圖。

    各試劑單位不同（mL、g、支），直接比剩量沒有意義，所以改用百分比；
    低於 100%（低於安全庫存）的標成紅色。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    for f in ["Noto Sans CJK TC", "Microsoft JhengHei", "PingFang TC", "Heiti TC", "Arial Unicode MS"]:
        if any(f == x.name for x in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = f
            break

    s = inv.summary()
    s = s[s.safety_stock > 0].copy()
    s["pct"] = (s.total / s.safety_stock * 100).round(0)
    s = s.sort_values("pct")
    colors = ["#C0392B" if p < 100 else "#2E86C1" for p in s.pct]

    fig, ax = plt.subplots(figsize=(8, 0.45 * len(s) + 1.5))
    ax.barh(s["name"], s.pct.clip(upper=400), color=colors)
    ax.axvline(100, color="#555", linestyle="--", linewidth=1)
    ax.text(101, len(s) - 0.4, "安全庫存線 100%", fontsize=9, color="#555")
    for y, (p, t, u) in enumerate(zip(s.pct, s.total, s.unit)):
        ax.text(min(p, 400) + 3, y, f"{p:.0f}%（{t:g} {u}）", va="center", fontsize=9)
    ax.set_xlabel("可用量占安全庫存量的百分比（%）")
    ax.set_title(f"LabStock 庫存狀態（{inv.today.isoformat()}）紅色＝低於安全庫存")
    ax.set_xlim(0, 480)
    fig.tight_layout()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out
