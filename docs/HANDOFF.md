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

### 已知未修的正確性問題（接手後的第一優先）

`_oracle_latest()` 取「最新值」的 SQL 是：

```sql
WHERE TAGNAME IN (...)
AND DATETIME = (SELECT MAX(DATETIME) FROM ... WHERE TAGNAME IN (...))
```

這是**整批 tag 共用一個全域 MAX(DATETIME)**。如果批次裡某個 tag 更新
頻率較慢、在那個精確時間點沒有樣本，它就查不到列，結果回 `value: null`
——明明有較舊的最新值卻拿不到。應改成 per-tag latest，例如：

```sql
SELECT TAGNAME, VALUE, DATETIME FROM (
  SELECT TAGNAME, VALUE, DATETIME,
         ROW_NUMBER() OVER (PARTITION BY TAGNAME ORDER BY DATETIME DESC) rn
  FROM {table} WHERE TAGNAME IN (...)
) WHERE rn = 1
```

改動後需使用者在廠內對照 SCADA 畫面實測（見 §6 驗證限制）。

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

分支 `claude/project-handoff-docs-nexorj` 比 `origin/main` 多 **7 個
commit（a3a3977..217ab08）**，內容就是整套 GMS 工具（`gms.py` 427 行、
`database.run_select` 抽出、server/README 對應修改）。**離線部署是從
main 打包的，所以正式廠務機上目前沒有 GMS 功能。**

接手後的第一件事（經使用者確認後）：把這 7 個 commit 合併回 `main`，
然後在廠內走一次 §1 的部署更新流程。

### 其他現狀

- **沒有任何測試**：`pyproject.toml` 配好了 pytest（`testpaths=["tests"]`），
  但 `tests/` 目錄不存在。也沒有 CI。
- README 的 Project Structure 段落已過時（缺 `gms.py`、`presentation.py`、
  `scripts/`、`.claude/`；工具清單倒是最新的）。
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

1. **【bug】修 `gms.py` `_oracle_latest` 的全域 MAX(DATETIME) 問題**
   （詳見 §3 末段，含建議 SQL）。改完請使用者廠內實測：挑一批含
   高頻+低頻 tag 的清單，確認低頻 tag 不再回 null。

2. **【品質】建立最小測試套件**。不碰真 DB 也能測的純邏輯：
   - `gms.py`：`_zone`（K1x/K2x/其他）、`_system_from_tag`、
     `_oracle_table`、`_chunk`、`_in_clause`、`_parse_dt`、
     history clamp 邏輯。
   - `presentation.py`：`_build_outline`（裁剪/擴充/priority 保留）、
     `_audit_slides`、`_word_count`（CJK）。
   - `database.py`：`_parse_mssql_dsn`、`_parse_oracle_dsn`。
   - `config.py`：`check_path`（白名單、write 開關）、`validate_config`
     （JDBC 擋下、缺 base_url）。注意 config 在 import 時載入，測試需
     用 `MCP_CONFIG` 指向 fixture 或重構載入時機（見第 7 項）。
   - DB 整合測試可用 SQLite 走 `run_select` / `db_execute` 全流程。

3. **【文件】更新 README**：Project Structure 段補上 `gms.py`、
   `presentation.py`、`scripts/`、`.claude/`、`docs/`；`db_execute`
   docstring 仍寫「SQLite and PostgreSQL」，實際支援 MSSQL、拒絕 Oracle。

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

## 8. 快速索引

| 想知道… | 看這裡 |
|---------|--------|
| 怎麼加一個工具 / 工具類別 | README「Adding New Tools」、`custom.py` 檔頭註解 |
| 怎麼接一個新的外部 API | README「Adding an API」、`api.py` 檔頭註解 |
| 空壓查詢的資料流與規則 | 本文件 §3、`gms.py` 檔頭 docstring |
| 簡報製作的完整流程 | `.claude/commands/create-presentation.md` |
| 離線部署細節 | README「Offline / air-gapped (Windows)」、`scripts/pack_offline.ps1` / `install_offline.ps1` |
| 設定檔所有選項 | `config.toml.example`（含 MSSQL/Oracle/Push+ 範例） |
| 行為守則 | `CLAUDE.md` |
