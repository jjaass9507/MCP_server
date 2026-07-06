# 專案交接文件（HANDOFF）

> 這份文件是寫給接手本專案的 AI 助手（Sonnet 5）與維護者的完整交接說明。
> 讀完這一份，你應該知道：這個專案是什麼、為什麼長這樣、踩過哪些雷、
> 現在的狀態、以及接下來該做什麼。所有內容都以 2026-07 的程式碼與
> git 歷史為準；若與程式碼衝突，以程式碼為準。

---

## 1. 專案定位與部署環境

### 這是什麼

一個 **Python FastMCP server**，讓 AI agent（Claude Desktop、或地端模型透過
Open WebUI 的 SSE 介面）安全地存取廠務（工廠設施）環境的資料與服務。
核心設計原則：**模型只看得到「別名」，機密與實際位置全部留在 server 端**——
檔案路徑走 `allowed_paths` 白名單、資料庫走 `db_name` 別名、外部 API 走
service 名別名，token/DSN/api_key 一律由 `config.toml` 注入、絕不進 log。

工具分六類（`src/mcp_server/tools/`）：

| 模組 | 工具 | 用途 |
|------|------|------|
| `filesystem.py` | `read_file` / `write_file` / `list_directory` / `search_files` / `file_info` / `delete_file` / `fs_list_allowed_paths` | 白名單內的檔案存取 |
| `database.py` | `db_query` / `db_execute` / `db_list_databases` / `db_list_schemas` / `db_list_tables` / `db_table_schema` / `db_execute_script` | SQLite / PostgreSQL / MSSQL / Oracle（Oracle 強制唯讀） |
| `gms.py` | `gms_list_equipment` / `gms_list_points` / `gms_list_pipe_points` / `gms_realtime_values` / `gms_history_values` | 空壓系統點位/即時/歷史值查詢（本專案最重要的領域工具，見 §3） |
| `api.py` | `api_list_services` / `api_request` / `push_notify` | config 驅動的外部 HTTP API；Push+ 2.0 通知 |
| `presentation.py` | `list_presentation_styles` / `plan_presentation_outline` / `create_presentation` / `verify_presentation` | 透過 Node.js pptxgenjs 產生可編輯的 .pptx |
| `custom.py` | `echo` / `system_info` / `calculate` / `format_data` | 工具範本 + 連線測試 |

### 部署環境（重要：離線 Windows）

正式環境是**無網路（air-gapped）的 Windows 廠務機**。部署流程：

1. 線上開發機：`git pull` 到最新 → 執行 `scripts/pack_offline.ps1`。
   它直接從 `pyproject.toml` 讀依賴清單下載 wheels（新增依賴免改腳本），
   連同 pptxgenjs 的 `node_modules` 一起打成 `..\mcp-server-offline.zip`。
2. 離線目標機：解壓 → `scripts/install_offline.ps1`。它會**重建** `.venv`
   （venv 不可跨機器搬移），並用 `.pth` 檔把 venv 指向 live 的 `src/` 目錄。
3. 之後**只要 `git pull`（或解新 zip）就完成更新，不用重裝**——除非
   `pyproject.toml` 的依賴變了，才需要重跑 pack/install。

執行方式：

```
python -m mcp_server.server                                  # stdio（Claude Desktop）
python -m mcp_server.server --transport sse --port 8080      # SSE（Open WebUI）
```

**server 必須從專案根目錄啟動**：`config.py` 以 cwd 找 `config.toml`
（可用 `MCP_CONFIG` 環境變數覆寫），`presentation.py` 以 cwd 找
`scripts/generate_pptx.js`。

Logging 用環境變數控制（不在 config.toml）：`MCP_LOG_LEVEL`、`MCP_LOG_FILE`。
Log 一律走 **stderr**（stdout 保留給 stdio transport 的 JSON-RPC）。

---

## 2. 架構與慣例

- `server.py`：組裝入口。每個工具模組提供 `register(mcp, cfg)`，在
  `create_server()` 內逐一註冊。新增工具類別 = 新增一個模組 + 在
  `server.py` 加兩行（README「Adding New Tools」有範例）。
- `config.py`：**在 import 時**載入 `config.toml`，提供存取控制的三個核心
  函式——`check_path()`（檔案白名單 + 寫入開關）、`resolve_db()`（db 別名
  → DSN）、`resolve_api()`（service 別名 → 設定 dict）。啟動時
  `validate_config()` 會把設定錯誤（不存在的 allowed_path、JDBC URL 等）
  擋在啟動階段，而不是等第一次工具呼叫才爆。
- 錯誤處理：使用者該看到的錯誤一律 `raise ToolError("訊息")`
  （`utils/errors.py`）；GMS 工具的錯誤訊息用中文。
- 寫入類操作（`write_file`、`delete_file`、`db_execute`、
  `db_execute_script`）記 INFO log 供稽核；**token、api_key、訊息內容
  永不進 log**。
- **docstring 是給模型看的操作說明書**：每個 `@mcp.tool()` 的 docstring
  要寫清楚使用時機、參數格式、先呼叫哪個 discovery 工具
  （`db_list_databases` / `api_list_services` / `fs_list_allowed_paths` /
  `list_presentation_styles`）。這個專案大量心力花在把「agent 會犯的錯」
  寫進 docstring 預防，改工具時請延續這個做法。
- 行為守則見 `CLAUDE.md`：surgical changes、簡單優先、先問再假設。

### 資料庫層設計決策

- DSN 依 scheme 分流：`postgresql://`→psycopg、`mssql://`→pymssql、
  `oracle://`→oracledb、其他視為 SQLite 檔案路徑。
- **Oracle 強制唯讀**：`db_execute` / `db_execute_script` 對 Oracle 連線
  直接拒絕（廠內 SCADA 資料庫絕不可寫）。
- JDBC URL 在啟動驗證直接擋下並提示正確的 Python DSN 格式
  （曾有人把 `jdbc:sqlserver://...` 貼進 config）。
- MSSQL 預設 schema 是 `dbo`：工具收到預設值 `public` 時自動映射成 `dbo`。
- Oracle 的 "schema" 是 owner（使用者名），識別字自動轉大寫。

---

## 3. GMS 空壓查詢工具（核心領域邏輯，務必讀懂）

`gms.py` 把原本「空壓系統點位查詢助理」prompt 裡要求 agent 每次自己拼 SQL
的固定領域邏輯，收斂成五個參數化唯讀工具。這是整個專案演進最多次、
踩雷最多的部分——以下每一條都對應一個真實修過的 bug。

### 資料架構

- **點位主檔在 PostgreSQL**：連線名 **`postgreSQL_CIM`**（寫死在
  `gms.py` 的 `CATALOG_DB`），schema `"GMS_agent"`，用到三個 view/table：
  `v_equipment_list`（設備主檔）、`v_point_detail`（點位/tag 明細）、
  `pipe_point`（管網點位）。
- **即時/歷史值在 Oracle SCADA**：連線名 **`oracle`**（寫死在
  `REALTIME_DB`），表名規則
  `FACCIMTAB.ZONE{1|2}_{building}_{GMS|PMS}`，欄位只有
  `TAGNAME / VALUE / DATETIME`。
- ⚠️ **config.toml 的 `[database.connections]` key 必須與這兩個寫死的
  名字完全一致**，否則 GMS 工具全數失效。

### 固定領域規則（寫死在程式裡）

| 規則 | 實作 |
|------|------|
| 廠棟 → Zone | `K1x` → ZONE1，`K2x` → ZONE2（其他直接報錯） |
| Tag → 系統表 | tag 含 `_GMS_` → GMS 表；含 `_PMSH_` 或 `_PMS_` → PMS 表 |
| Tag 分批 | 每批 10 個（`_chunk`），依系統表分組後分批查 |
| 歷史上限 | 1 天；超過自動 clamp 到 end_time 往前 1 天，回 `adjusted=true`（原本 3 小時，217ab08 放寬） |

### 工具契約（曾因違反而出 bug，不要改壞）

1. **`gms_realtime_values` / `gms_history_values` 只吃已確認的
   `tag_names`**——不做 device_id/category/keyword 搜尋。正確流程是先
   `gms_list_points` 解析出 tag，再拿 tag 查值。歷史上曾允許模糊輸入，
   造成查錯設備，最後收斂成純查值（commit d98f03d）。
2. **`building + device_id` 不唯一**：同一棟的 `A4` 可能同時是空壓機和
   乾燥機。消歧義靠 `category`（大類：空壓機/乾燥機/真空機）與
   `equipment_type`（細類：離心機/變頻螺旋機），且必須**直接在
   `v_point_detail` 上過濾**——join `v_equipment_list` 無法區分同編號
   設備的點位（commit d8482fb 修過這個 bug）。
3. **Oracle 時間參數必須 bind 原生 `datetime` 物件，不能 bind 字串**。
   bind 字串會走 NLS_DATE_FORMAT 隱式轉換，session 設定不同就炸
   ORA-01843（commit ec7e3f7）。

### 已修正的正確性問題（待廠內實測）

`_oracle_latest()` 原本的 SQL 用**整批 tag 共用的全域 MAX(DATETIME)**
——批次裡更新頻率較慢的 tag 在該精確時間點沒有樣本就回 `value: null`，
明明有較舊的最新值卻拿不到。已於 2026-07-03（commit `32173a1`）改為
per-tag latest：

```sql
SELECT TAGNAME, VALUE, DATETIME FROM (
  SELECT TAGNAME, VALUE, DATETIME,
         ROW_NUMBER() OVER (PARTITION BY TAGNAME ORDER BY DATETIME DESC) rn
  FROM {table} WHERE TAGNAME IN (...)
) WHERE rn = 1
```

⚠️ **尚未在真實 Oracle 上驗證**：請使用者在廠內用一批「高頻 + 低頻混合」
的 tag 清單跑 `gms_realtime_values`，確認低頻 tag 不再回 null、值與
SCADA 畫面一致（見 §6 驗證限制）。

---

## 4. 其他工具的設計決策與踩雷紀錄

### api.py / push_notify

- token / base_url / auth header 全部 server 端注入，模型只看 service 名。
- **圖片附件走 `image_path`（server 讀檔轉 base64），絕不讓 agent 自己
  輸出 base64**——一張圖的 base64 會直接爆掉模型的 output token 上限
  （commit 8f7fa4f 就是為此改的）。上限 5 MB，路徑受 `check_path` 管制。
- `content` 強制 inline HTML 格式，回傳 echo `sent_content`（不含 base64）
  讓 agent 自我驗證送出的內容正確（commit 6e3b97f）。
- 內網自簽憑證的服務在 service block 設 `verify = false`。
- 回應 body 截斷在 100 KB，避免大 payload 灌爆 context。

### presentation.py（三步流程）

1. `list_presentation_styles()` — 8 個 preset、12 種版型、63 個 Lucide
   icon、slides_json 格式全文。
2. `plan_presentation_outline(topic, slide_count, deck_type)` — 依
   deck_type（general / product_pitch / technical / project_status /
   training）回逐頁 scaffold。頁數少於框架時按 priority 裁剪（priority 1
   永不刪、被刪頁的主題折進前一頁的 guidance）；多於框架時用該類型的
   擴充頁補。
3. `create_presentation(slides_json, output_path)` — 寫暫存 JSON → 呼叫
   `node scripts/generate_pptx.js` → 驗證檔案真的存在並回報大小。

踩雷紀錄：

- **style 必須放在 `slides_json["style"]` 裡面**。獨立參數
  `style_preset`/`title_font`/`body_font` 只是 fallback，顏色根本帶不進去
  ——曾發生使用者的自訂色碼被 silently ignore（commit 20db477、02c241a
  之後 docstring 大幅強化這點）。
- `output_path` 必須是 **Windows 絕對路徑**且在 allowed_paths 內；
  `/tmp` 之類的 Linux 路徑會被拒（agent 平台的習慣路徑會誤導模型）。
- generator 的 `OK:{path}:{count}:{preset}` 輸出要用 `rsplit(":", 2)`
  解析——Windows 路徑的磁碟機代號本身含冒號（commit 26ba5b0）。
- 產出後驗證 `out.exists()` 並回報實際 KB 數，讓 agent 無法幻覺成功。
- 內容太稀疏會回非阻斷的 `CONTENT QUALITY NOTES`（`_audit_slides`，
  規則借鑑 Presenton 的密度標準），deck 照樣產出，由 agent 決定要不要
  補內容重產。
- `verify_presentation`（pptx → PNG 視覺 QA）需要 LibreOffice，是選配；
  Georgia / Trebuchet MS 字型在 LibreOffice 下會被替換，寬度不準。
- `.claude/commands/create-presentation.md` 是引導式製作流程 skill
  （逐步跟使用者確認主題→風格→大綱→內容→輸出路徑），改工具介面時
  記得同步這份文件。
- config.toml 的 `[presentation]` 可設 `default_preset` 等預設值，
  免得 agent 每次自己猜風格。

---

## 5. 目前狀態（2026-07-03，最重要的交接事項）

### ⚠️ GMS 工具還沒合併回 main

分支 `claude/project-handoff-docs-nexorj` 比 `origin/main` 多 **11 個
commit**：7 個 GMS 工具功能（a3a3977..217ab08，`gms.py` 427 行、
`database.run_select` 抽出）＋ 本交接文件 ＋ `_oracle_latest` 修正 ＋
測試套件 ＋ README/docstring 更新。**離線部署是從 main 打包的，所以
正式廠務機上目前沒有 GMS 功能。**

接手後的第一件事（經使用者確認後）：把這些 commit 合併回 `main`，
然後在廠內走一次 §1 的部署更新流程。

### 其他現狀

- 測試：2026-07-03 起有 39 個純邏輯測試（`tests/`，不需真實 DB，
  `pytest` 即可跑）。仍**無 CI、無 DB 整合測試**，config.py 也尚未
  納入測試（見 §7 第 2、7 項）。
- `config.toml` 不進版控（gitignored），正式機上的實際設定只存在於
  廠內機器，內含 DSN 與 Push+ token。

---

## 6. 給接手 AI 的工作守則與驗證限制

### 你能驗證什麼、不能驗證什麼

開發環境**連不到廠內的 PostgreSQL / Oracle / Push+ / Windows 檔案系統**。
你能做的驗證：

- 純邏輯單元測試（見 §7 優化方向 2 的清單）。
- 匯入與啟動檢查：`python -c "import mcp_server.server"`、
  `cfg.validate_config()`。
- `generate_pptx.js --test`（有 Node.js 時）。

**任何涉及廠內 DB / SCADA / Push+ 的行為改動，最終都要請使用者在廠內
實測回報**。改 SQL 時把「請幫我用這幾個參數跑一次、貼回結果」的具體
測試步驟寫給使用者。

### 慣例摘要

- 遵守 `CLAUDE.md`：只改該改的、不順手重構、先問再假設。
- 新工具的 docstring 要預防 agent 誤用（何時用、先呼叫哪個 discovery
  工具、參數格式範例）。
- `ToolError` 訊息面向使用者；GMS 類用中文。
- 不 log 機密；寫入操作記 INFO。
- 部署更新 = 合併到 main → 使用者在廠內 `git pull`；依賴變動才需重跑
  `pack_offline.ps1` / `install_offline.ps1`。

---

## 7. 優化方向 roadmap（按優先序）

> **2026-07-03 提出、2026-07-06 已實作**：大量查詢結果的檔案快取
> （file-based handoff），完整需求、可行性評估、設計與**實作狀態**見
> **§8**（commit `b72d93f`/`bd302ed`/`cc90fcb`）。待廠內實測通過後再繼續
> 下面未完成的 4–7 項。

1. ✅ **已完成（2026-07-03，commit `32173a1`）**：`_oracle_latest` 改為
   per-tag latest（見 §3）。仍待使用者廠內實測：挑一批含高頻+低頻 tag
   的清單，確認低頻 tag 不再回 null。

2. ✅ **大致完成（2026-07-03，commit `b765b82`）**：`tests/` 下 39 個
   純邏輯測試（gms / presentation / database DSN 解析），`pytest` 全綠。
   尚未涵蓋：`config.py` 的 `check_path`/`validate_config`（config 在
   import 時載入，需用 `MCP_CONFIG` 指向 fixture 或先重構載入時機，見
   第 7 項）、用 SQLite 跑 `run_select`/`db_execute` 的整合測試、CI。

3. ✅ **已完成（2026-07-03，commit `a29ba14`）**：README Project
   Structure 與 `database.py` 過時 docstring 均已更新。

4. **【效能】連線池/快取**：目前每次工具呼叫都新建 DB 連線，Oracle
   建線特別慢，GMS 一次查詢又會多批次呼叫。可在 `database.py` 加簡單的
   per-DSN 連線快取（注意執行緒安全與斷線重連）。

5. **【功能】`db_query` 的 `SELECT` 前綴檢查擋掉 CTE**：
   `WITH x AS (...) SELECT ...` 是合法唯讀查詢卻被拒。放寬時維持唯讀
   保證（例如允許 `WITH` 開頭但整串只含 SELECT）。

6. **【升級注意】SSE transport 已被 MCP spec 淘汰**（新標準是
   streamable HTTP）。升級 `mcp` 套件時 Open WebUI 的接法可能要跟著改。
   另外 SSE 端點無認證、預設 bind `0.0.0.0`——目前在隔離內網可接受，
   但如果部署環境改變要先處理。

7. **【重構，順手做即可】**
   - `config.py` 在 import 時載入設定，難以測試；可改成顯式
     `load()` + 注入。
   - GMS 的連線名 `postgreSQL_CIM` / `oracle` 寫死，可移到 config
     （移的話記得同步 config.toml.example 與廠內 config）。
   - `custom.py` 的 `calculate` 用受限 `eval`，理論上可用 attribute
     chain 逃逸（`(1).__class__...`）；信任環境下低風險，改 `ast`
     求值即可根治。
   - Secrets（DSN 密碼、Push+ token）明文存 config.toml——air-gapped
     環境下可接受，文件註明即可，別過度工程。

---

## 8. 已實作（待廠內實測）：大量查詢結果的檔案快取（file-based handoff）

> 使用者 2026-07-03 提出，2026-07-06 依 §8.3 的設計實作完成，commit：
> `b72d93f`（export 基礎 + `db_query_to_file` + `gms_history_values(to_file)`）、
> `bd302ed`（export 路徑測試）。原 `cc90fcb`（第二階段 `plot_csv` 繪圖工具，
> 新增 `matplotlib` 依賴，含測試）**已於 2026-07-06 依使用者要求移除**——
> 檔案快取本體（`[export]`、`db_query_to_file`、
> `gms_history_values(to_file=true)`）維持不變，只拿掉繪圖功能，
> `matplotlib` 依賴也一併移除。
>
> **開發機已驗證**：`pytest` 53 個測試全綠（export 測試仍在，plotting
> 測試已隨功能一併移除）。
>
> **尚待廠內實測**（開發機無法驗證的項目，見 §6「你能驗證什麼」）：
> 1. CSV 用 Windows Excel 直接開啟，中文欄名/內容正常（utf-8-sig BOM）。
> 2. `[export] dir` 指向真實的廠內 Windows 路徑後，`db_query_to_file` /
>    `gms_history_values(to_file=true)` 的端對端串接（查詢 → CSV）尚未跑過。
>
> 實作前已確認的設計取捨見 §8.3（export 目錄路徑等仍以使用者實際廠內
> 路徑為準，目前 `config.toml.example` 用 Windows 占位路徑示範）。

### 8.1 問題

`gms_history_values`（1 天 × N tags 的 series）和大範圍的 `db_query`
會把整包資料轉成 JSON 塞回 LLM context——token 成本高、可能直接超過
上限，而且 agent 後續要拿資料畫圖/分析時，資料進 context 根本沒有用處。

### 8.2 使用者提出的做法 A（文件快取法）與可行性評估

做法 A：查詢工具不回傳資料本體，改把結果存成暫存檔（如 CSV），只回傳
「檔案路徑 + schema」給 LLM；後續由 Python 工具直接讀檔處理（例如畫
關聯矩陣）。

**評估結論：可行性高，建議採用**，理由：

- 與現有架構天然契合：`push_notify` 已有「檔案留在 server、context 只
  走路徑」的成功先例（`image_path` 由 server 讀檔轉 base64，見 §4）；
  輸出目錄可直接用 `allowed_paths` / `check_path` 機制管制。
- 資料從頭到尾不進 context，token 成本從 O(資料量) 降為 O(1)。

**但有一個前提缺口**：本 server **沒有**通用 Python 執行工具，做法 A
第三步「LLM 呼叫 Python 工具讀檔畫圖」目前不存在。處理方式見 8.3 第
4、5 點——**不要**為此加任意程式碼執行工具。

**Token 安全關鍵**：工具回傳只能含「路徑 + 欄位 + 筆數 + 前幾筆
preview +（GMS）per-tag summary」；且 docstring 必須明確警告 agent
**不要用 `read_file` 把整個 CSV 讀回 context**（否則前功盡棄）。

### 8.3 建議設計（新增工具為主、小幅修改為輔）

1. **新 config 區塊 `[export]`**：`dir = "D:/.../mcp_exports"`（實際
   路徑請使用者提供）。啟動驗證目錄存在，且必須位於 `allowed_paths`
   內（或自動納入）。檔名帶 timestamp（如
   `query_20260703_153000.csv`）；每次寫入前清理超過 N 天的舊檔
   （建議 7 天，避免離線機磁碟被暫存檔塞滿）。CSV 編碼用
   **utf-8-sig**（Windows Excel 直開中文不亂碼）。

2. **新增 `db_query_to_file(db_name, sql, params, filename="")`**
   （`database.py`）：執行 SELECT、寫 CSV，回傳
   `{path, columns, row_count, preview(前 5 筆), size_kb}`。
   **不改 `db_query`**——小結果直接回傳仍是最方便的路徑，docstring
   互相指引（「結果可能上千列時改用 db_query_to_file」）。

3. **`gms_history_values` 加選用參數 `to_file: bool = False`**：
   預設行為 100% 不變（向後相容，不破壞既有工具契約，見 §3）。
   `to_file=true` 時把 series 寫成 CSV（欄位：`tag_name, point_name,
   phase, unit, datetime, value`），回傳保留 `adjusted` /
   `start_time` / `end_time` / 每 tag 的 `summary`（max/min/latest），
   把 `series` 換成檔案資訊。summary 本來就小又有價值，一定要留。
   （若想完全不碰既有函式，替代方案是另開 `gms_history_to_file`
   工具；二選一即可，建議前者——少一個工具、參數預設值保證相容。）

4. **第二階段（獨立 commit）：固定功能繪圖工具**，補上做法 A 的
   「Python 工具」缺口。例如
   `plot_csv(csv_path, chart_type, x, y_columns, output_png)`，用
   matplotlib 畫折線/散佈/相關矩陣，輸出 PNG 路徑。PNG 可直接餵
   `push_notify(image_path=...)`，完成「查詢 → 存檔 → 畫圖 → 推播」
   全程資料不進 context 的閉環。
   ⚠️ matplotlib 是新依賴：改 `pyproject.toml` 後，廠內部署需重跑
   `pack_offline.ps1` / `install_offline.ps1`（見 §1）。中文圖表
   需指定字型（Microsoft JhengHei），離線機上要驗證。

   **【已移除】2026-07-06 依使用者要求移除 `plot_csv`（原 commit
   cc90fcb）與 `matplotlib` 依賴**：本點所述設計仍保留於此作為歷史紀錄，
   但目前不適用——檔案快取（第 1–3 點）維持不變；若未來需要畫圖功能，
   需重新評估設計與依賴。

5. **明確不建議**：通用 `exec_python`（任意程式碼執行）工具。廠務機
   上風險過高，也違反本專案「把固定領域邏輯收斂成參數化工具」的路線
   （§3 的教訓）。若未來真的需要彈性分析，再評估受限的 pandas 表達式
   工具，且必須先與使用者確認。

### 8.4 驗收標準

- 查 1 天 × 10 tags 的歷史資料，工具回傳給 agent 的內容 < 1 KB，
  CSV 檔案內容完整正確。
- 既有呼叫（不帶 `to_file`）行為完全不變：既有 39 個測試全綠，
  並為新路徑補測試（CSV 寫出、preview 截斷、舊檔清理）。
- 廠內實測：CSV 用 Excel 直開中文欄位正常。
  （第二階段 PNG 圖表驗收標準已隨 `plot_csv` 於 2026-07-06 移除，不適用
  ——見上方實作狀態段落與 §8.3 第 4 點。）

---

## 9. 快速索引

| 想知道… | 看這裡 |
|---------|--------|
| 怎麼加一個工具 / 工具類別 | README「Adding New Tools」、`custom.py` 檔頭註解 |
| 怎麼接一個新的外部 API | README「Adding an API」、`api.py` 檔頭註解 |
| 空壓查詢的資料流與規則 | 本文件 §3、`gms.py` 檔頭 docstring |
| 簡報製作的完整流程 | `.claude/commands/create-presentation.md` |
| 離線部署細節 | README「Offline / air-gapped (Windows)」、`scripts/pack_offline.ps1` / `install_offline.ps1` |
| 設定檔所有選項 | `config.toml.example`（含 MSSQL/Oracle/Push+ 範例） |
| 行為守則 | `CLAUDE.md` |
