"""通知模組（F4）：組成低庫存／到期提醒信，並以 smtplib 寄出。

- config.ini 啟用時：透過 SMTP（預設 Gmail，需應用程式密碼）寄給實驗室信箱。
- 未啟用（測試模式）或寄送失敗：把信件內容存到 outbox/ 資料夾，畫面同時顯示提醒，
  確保提醒不會因為網路或帳號問題而消失。
"""
from __future__ import annotations

import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import pandas as pd

from .config import EmailSettings


def low_stock_message(rows: pd.DataFrame, today) -> tuple[str, str]:
    lines = [f"LabStock 低庫存提醒（{today}）", "",
             "以下試劑的可用量已低於安全庫存量，請負責採購的同學確認是否需要下單：", ""]
    for _, r in rows.iterrows():
        lines.append(f"- {r['name']}：剩 {r.total:g} {r.unit}（安全量 {r.safety_stock:g} {r.unit}），"
                     f"位置：{r.location or '未填'}")
    lines += ["", "提醒：下單前可先在 LabStock 查詢，避免重複訂購。", "— LabStock 自動通知"]
    return f"[LabStock] {len(rows)} 項試劑低於安全庫存", "\n".join(lines)


def expiry_message(rows: pd.DataFrame, today) -> tuple[str, str]:
    lines = [f"LabStock 效期提醒（{today}）", "", "以下試劑即將到期或已過期，請優先使用或處理：", ""]
    for _, r in rows.iterrows():
        when = f"已過期 {-r.days_left} 天" if r.days_left < 0 else f"{r.days_left} 天後到期"
        lines.append(f"- {r['name']} #{int(r.bottle_no)}：效期 {r.expiry_date}（{when}），"
                     f"剩 {r.remaining_qty:g} {r.unit}")
    lines += ["", "— LabStock 自動通知"]
    return f"[LabStock] {len(rows)} 瓶試劑即將到期或已過期", "\n".join(lines)


class Notifier:
    def __init__(self, settings: EmailSettings, outbox_dir: Path | str):
        self.settings = settings
        self.outbox = Path(outbox_dir)

    def send(self, subject: str, body: str) -> str:
        """回傳結果說明：'sent' / 'dry-run: 檔案路徑' / 'failed: 原因（已存檔 路徑）'。"""
        s = self.settings
        if not (s.enabled and s.sender and s.password and s.recipients):
            return f"dry-run: {self._save(subject, body)}"
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, s.sender, ", ".join(s.recipients)
        msg.set_content(body)
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
                smtp.starttls()
                smtp.login(s.sender, s.password)
                smtp.send_message(msg)
            return "sent"
        except (smtplib.SMTPException, OSError) as e:
            return f"failed: {type(e).__name__}（已存檔 {self._save(subject, body)}）"

    def _save(self, subject: str, body: str) -> Path:
        self.outbox.mkdir(parents=True, exist_ok=True)
        p = self.outbox / f"{datetime.now():%Y%m%d_%H%M%S_%f}.txt"
        p.write_text(f"主旨：{subject}\n收件人：{', '.join(self.settings.recipients) or '（未設定）'}\n\n{body}\n",
                     encoding="utf-8")
        return p
