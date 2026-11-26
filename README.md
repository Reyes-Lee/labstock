# LabStock：實驗室試劑庫存管理與低庫存提醒系統

第 E 組 Python 期末專題。讓實驗室成員在「分裝入庫」與「領用」時登錄數量，系統自動算出每種試劑、每一個包裝（瓶、管、包、罐…）的剩量，依先進先出建議要用哪一個，並在庫存跌破安全量或即將過期時寄電子郵件提醒。

提供兩種介面，共用同一份資料與同一套規則：
- **網頁版（主要介面）**：在實驗室電腦執行後用瀏覽器操作；同一網路的其他電腦也能用網址開啟。
- **文字選單（備用）**：不需瀏覽器，適合只有終端機的電腦或快速測試。

## 環境需求
- Python 3.10 以上
- 套件：`pip install -r requirements.txt`（pandas、matplotlib、streamlit；測試用 pytest）

## 快速開始

### 網頁版
```bash
# 示範：載入範例資料到 data/demo，並以 2026-11-27 當作今天（方便重現示範結果）
streamlit run web_app.py -- --data data/demo --load-sample --today 2026-11-27

# 實際使用：資料預設存在 data/lab/
streamlit run web_app.py
```
啟動後瀏覽器會開啟 http://localhost:8501。若要讓同一網路的其他人使用，改用
`streamlit run web_app.py --server.address 0.0.0.0`，其他人以 `http://<這台電腦的 IP>:8501` 開啟。

網頁版功能（左側選單）：總覽、查詢、領用、撤銷領用、入庫、品項管理（新增／編輯／刪除）、提醒清單、統計與匯出（下載 CSV 報表與圖表）。

### 文字選單
```bash
python main.py --data data/demo --load-sample --today 2026-11-27
python main.py
```

| 選項 | 功能 | 對應需求 |
|---|---|---|
| 1 | 查詢／搜尋試劑、排序、查看各包裝與紀錄 | F5 |
| 2 | 領用試劑（扣量，依先進先出建議，跌破安全量自動寄提醒） | F3、F4、F6 |
| 3 | 入庫（新貨分裝，自動編序號） | F2 |
| 4 | 新增試劑品項（含包裝單位） | F1 |
| 5 | 刪除試劑品項（停用，保留歷史紀錄） | F1 |
| 6 | 低庫存與到期清單、寄出提醒 | F4 |
| 7 | 庫存統計與圖表（輸出到 output/） | F5 |
| 8 | 匯出庫存報表與包裝明細 CSV（輸出到 output/） | F6 |
| 9 | 撤銷領用紀錄（登錄錯誤時加回用量） | F3 |
| 10 | 編輯試劑品項（安全量、位置、包裝單位…） | F1 |
| 0 | 離開 | |

每天第一次開啟程式（或網頁）時，若有低庫存或 30 天內到期的試劑，會自動寄一封每日提醒。

## 電子郵件設定（選用）
1. 複製 `config.example.ini` 為 `config.ini`。
2. 填入寄件 Gmail、[應用程式密碼](https://support.google.com/accounts/answer/185833)與收件人，`enabled = true`。
3. `config.ini` 已列在 `.gitignore`，不會上傳 GitHub。

沒有設定時程式以「測試模式」運作：提醒信內容存到資料夾內的 `outbox/`，畫面也會顯示提醒。

## 資料檔
資料存成三個 CSV（UTF-8 BOM，Excel 可直接開啟）：

| 檔案 | 內容 |
|---|---|
| `reagents.csv` | 試劑品項：名稱、單位、包裝單位、存放位置、安全庫存量、廠商、是否停用 |
| `bottles.csv` | 每一個包裝：序號、原始量、剩量、入庫日、效期、狀態 |
| `usage_log.csv` | 操作紀錄：時間、操作者、動作（新增／編輯／入庫／領用／撤銷領用／刪除）、數量 |

每次寫入前會自動備份到 `backup/`（每個表保留最近 20 份）。網頁版多人同時操作時，所有寫入都經過同一個伺服器並逐一執行，不會互相覆蓋。

範例資料 `data/sample/` 是常見試劑的**測試用假設資料**，不是受訪實驗室的真實庫存；可用 `python scripts/build_sample_data.py` 重新產生。

## 測試
```bash
python -m pytest -v
```

## 程式結構
```
labstock/
├── web_app.py              網頁版介面（Streamlit）
├── main.py                 文字選單啟動檔
├── labstock/
│   ├── cli.py              文字選單介面
│   ├── inventory.py        核心邏輯：新增／編輯／停用、入庫編號、領用扣量、撤銷、先進先出、低庫存與到期判斷
│   ├── search.py           搜尋、排序、統計、庫存圖表（matplotlib）
│   ├── notifier.py         提醒信內容、寄送（smtplib）、每日提醒
│   ├── export.py           匯出中文欄位 CSV 報表
│   ├── storage.py          資料層：CSV 讀寫與自動備份（pandas）
│   └── config.py           路徑、預設值、讀取 config.ini
├── data/sample/            範例資料
├── scripts/build_sample_data.py
└── tests/test_labstock.py  25 個測試案例
```
