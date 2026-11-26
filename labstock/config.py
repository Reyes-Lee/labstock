"""設定模組：路徑、預設值與電子郵件設定的讀取。

電子郵件帳號密碼放在專案根目錄的 config.ini（已列入 .gitignore，不會上傳 GitHub）。
沒有 config.ini 或 enabled = false 時，系統以「測試模式」運作：提醒信寫入 outbox/ 資料夾。
"""
from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = BASE_DIR / "data" / "lab"
SAMPLE_DATA_DIR = BASE_DIR / "data" / "sample"
CONFIG_FILE = BASE_DIR / "config.ini"

EXPIRY_WARNING_DAYS = 30  # 幾天內到期要提醒
BACKUP_KEEP = 20          # 每個資料表保留的備份數


@dataclass
class EmailSettings:
    enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    sender: str = ""
    password: str = ""
    recipients: list[str] = field(default_factory=list)


def load_email_settings(path: Path = CONFIG_FILE) -> EmailSettings:
    """讀取 config.ini 的 [email] 區段；檔案不存在時回傳預設（測試模式）。"""
    settings = EmailSettings()
    if not Path(path).exists():
        return settings
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    if "email" not in parser:
        return settings
    sec = parser["email"]
    settings.enabled = sec.getboolean("enabled", fallback=False)
    settings.smtp_host = sec.get("smtp_host", settings.smtp_host)
    settings.smtp_port = sec.getint("smtp_port", fallback=settings.smtp_port)
    settings.sender = sec.get("sender", "")
    settings.password = sec.get("app_password", "")
    settings.recipients = [r.strip() for r in sec.get("recipients", "").split(",") if r.strip()]
    return settings
