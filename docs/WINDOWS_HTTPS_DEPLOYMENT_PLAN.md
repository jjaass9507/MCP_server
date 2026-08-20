# Windows + IIS + 公司內部 CA HTTPS 部署交接計畫

> **目的**：將目前部署於 Windows、必須依賴 PowerShell 視窗持續開啟的 HTTP MCP Server，轉換成可正式長期運行的內部服務：Windows 開機自動啟動、透過公司內部 CA 憑證提供 HTTPS、保留 Bearer Token 驗證，並支援跨機器的 HTTPS 匯出檔案下載。
>
> **適用對象**：維護人員、開發 Agent、部署 Agent、後續接手此專案的其他 AI Agent。
>
> **Repository**：`jjaass9507/MCP_server`
>
> **主要正式環境**：離線或受限網路的 Windows Server / Windows 主機
>
> **MCP Transport**：Streamable HTTP
>
> **交接原則**：新的 Agent 不需要知道原始對話內容，應可直接從本文件「目前狀態」中第一個尚未完成的項目開始執行。

---

## 0. 最終目標架構

正式部署完成後，架構應如下：

```text
AI Agent / MCP Client
        |
        | HTTPS 443
        | Authorization: Bearer <token>
        v
+--------------------------------------+
| IIS                                  |
| 公司內部 CA 憑證                     |
| TLS Termination / Reverse Proxy      |
|                                      |
| /mcp     -> 127.0.0.1:8080/mcp      |
| /exports -> 127.0.0.1:8081/exports  |
+-------------------+------------------+
                    |
                    | 僅本機 HTTP
                    v
+--------------------------------------+
| MCP Server Windows Service           |
| Python .venv                         |
| Streamable HTTP                      |
| 127.0.0.1:8080                       |
| BearerTokenMiddleware 持續啟用       |
+--------------------------------------+

可選的 Export Server：
127.0.0.1:8081 -> IIS /exports -> HTTPS 443
```

### 對外 URL 目標

```text
https://<MCP_FQDN>/mcp
https://<MCP_FQDN>/exports/<id>
```

示例：

```text
https://mcp.internal.example.com/mcp
```

若使用公司憑證，不應以 IP 作為正式 MCP URL，除非憑證的 SAN 明確包含該 IP。正式環境應優先使用公司簽發憑證所對應的 DNS / FQDN。

---

## 1. 目前 Repository 基準狀態

任何 Agent 開始修改前，都要先確認程式碼仍符合以下假設；若實際程式碼已不同，以最新程式碼為準，並更新本文件。

### 1.1 MCP HTTP Runtime

主要程式：

```text
src/mcp_server/server.py
```

預期目前行為：

- `FastMCP` 使用 `streamable_http_path="/mcp"`。
- Streamable HTTP 透過 `uvicorn.run(...)` 啟動。
- ASGI App 外層使用 `BearerTokenMiddleware` 做 Bearer Token 驗證。
- Uvicorn 本身目前沒有正式掛載 TLS 憑證。
- Server 正常停止時，`finally` 會呼叫資料庫 pool cleanup。

### 1.2 HTTP 設定

設定範例：

```text
config.toml.example
```

預期 `[http]` 主要欄位：

```toml
host = "127.0.0.1"
port = 8080
allowed_hosts = [...]
allowed_origins = [...]
bearer_token_env = "MCP_HTTP_BEARER_TOKEN"
```

### 1.3 Windows 常駐服務缺口

目前 Repository 已有 Linux systemd：

```text
deploy/install-systemd.sh
deploy/mcp-server.service
```

但尚未有完整 Windows Service 部署方案。

因此現在 Windows 上若使用類似：

```powershell
python -m mcp_server.server --transport streamable-http
```

則 PowerShell 視窗關閉後 Python Process 也會停止，這不是正式服務部署方式。

### 1.4 Export / Download 現況

程式：

```text
src/mcp_server/utils/download_server.py
```

目前跨機器 download URL 是由：

```text
advertise_host + download_port
```

產生 HTTP URL。

因此若：

```toml
serve_downloads = true
```

只把 `/mcp` 改成 HTTPS 還不完整，`/exports` 也必須一併納入正式 HTTPS 架構。

---

## 2. 不可隨意變更的架構決策

除非維護者明確要求改架構，後續 Agent 必須維持以下決策。

### 2.1 TLS 由 IIS 終止

正式環境不採用「Python/Uvicorn 直接持有公司憑證」作為主要方案。

原因：

- 公司憑證通常由 Windows Certificate Store 管理。
- Private Key 可能被設定為不可匯出。
- IIS 可直接使用 Windows Certificate Store 的憑證。
- 公司後續換證或自動更新憑證時，不需要修改 Python MCP 程式碼。
- TLS、安全協定與憑證生命週期交給 IIS 比較符合 Windows 企業環境管理方式。

因此架構固定為：

```text
外部：HTTPS -> IIS
內部：IIS -> HTTP 127.0.0.1
```

### 2.2 IIS 上線後 MCP Backend 必須只 Bind localhost

正式設定目標：

```toml
[http]
host = "127.0.0.1"
port = 8080
```

IIS Reverse Proxy 正常後，不應再讓 MCP Backend Port `8080` 直接暴露給公司 LAN。

### 2.3 Bearer Token 驗證必須保留

HTTPS 只解決：

```text
傳輸加密
Server 身分驗證
```

Bearer Token 解決：

```text
MCP Client 存取授權
```

所以正式流向必須仍為：

```text
Client
  -> HTTPS
  -> IIS
  -> Authorization: Bearer <token>
  -> BearerTokenMiddleware
  -> MCP
```

不可因為已經使用 HTTPS，就移除 `BearerTokenMiddleware`。

### 2.4 Client 必須信任公司 CA

Server 掛好公司憑證不代表 Client 一定會信任。

Client 端必須能建立完整信任鏈：

```text
公司 Root CA
  -> Intermediate CA（若有）
  -> MCP Server Certificate
```

正式解法不可使用：

```text
verify=False
忽略 SSL error
關閉 certificate verification
```

### 2.5 必須保留離線部署能力

正式 MCP Windows 主機視為：

```text
Air-gapped / Restricted Network
```

因此後續新增 WinSW 或其他 Runtime Dependency 時，必須整合到 offline package。

正式 Server 不可在安裝服務時即時上網下載 WinSW。

---

## 3. 完成定義（Definition of Done）

以下全部達成後，才能視為這次正式部署完成：

- [ ] Windows 開機後 MCP 自動啟動。
- [ ] 不登入 Windows 也能啟動 MCP。
- [ ] 關閉所有 PowerShell 視窗後 MCP 仍正常運行。
- [ ] MCP Backend 只監聽 `127.0.0.1:8080`。
- [ ] IIS 使用公司簽發憑證監聽 HTTPS 443。
- [ ] `https://<MCP_FQDN>/mcp` 可經 IIS 正常連到 MCP。
- [ ] 沒有 Bearer Token 時仍回 401。
- [ ] Bearer Token 錯誤時仍回 401。
- [ ] 正確 Bearer Token 可成功建立 MCP Session。
- [ ] Client 不需停用 SSL 驗證即可連線。
- [ ] Windows Reboot 後 MCP Service 自動恢復。
- [ ] Python Process Crash 後 Service 可自動 Restart。
- [ ] MCP Log 寫入持久化檔案，而不是只存在 PowerShell console。
- [ ] 原本 Database Connection Pool 的正常釋放機制沒有被破壞。
- [ ] 若啟用 Export，`download_url` 改為 HTTPS 且跨機器可存取。
- [ ] LAN Client 不需要直接連 `8080` / `8081`。
- [ ] 新 Windows 主機可依文件重複部署。

---

# Phase A — 蒐集正式環境基礎資訊

**負責角色**：Infra / Windows Administrator / 部署 Agent

**需要改程式**：否

**是否 Blocking**：是

在開始正式 IIS 配置以前，先取得以下實際資訊。

## A1. 必填環境值

請填寫：

```text
MCP_FQDN=
MCP_SERVER_HOSTNAME=
MCP_SERVER_IP=
MCP_HTTPS_PORT=443
MCP_BACKEND_HOST=127.0.0.1
MCP_BACKEND_PORT=8080
MCP_EXPORT_BACKEND_PORT=8081
CERTIFICATE_SUBJECT=
CERTIFICATE_THUMBPRINT=
CORPORATE_CA_NAME=
WINDOWS_SERVICE_ACCOUNT=
PROJECT_ROOT=
PYTHON_VENV_PATH=
CONFIG_TOML_PATH=
LOG_DIRECTORY=
```

例如專案路徑可能是：

```text
D:\FAC_Job\MCP_server
```

以上僅為範例，不可寫死成正式值。

## A2. DNS 驗證

從 MCP Client 所在機器執行：

```powershell
Resolve-DnsName <MCP_FQDN>
```

預期：

```text
<MCP_FQDN> -> MCP Server IP
```

若沒有 DNS Record，應先由公司 DNS 管理單位完成新增。

## A3. 公司憑證確認

在 MCP Windows Server：

1. 開啟 `certlm.msc`。
2. 進入：

```text
Local Computer
  -> Personal
  -> Certificates
```

3. 找到 MCP 要使用的公司憑證。
4. 確認：
   - 尚未過期。
   - 有 Private Key。
   - EKU 包含 Server Authentication。
   - SAN 包含 `<MCP_FQDN>`。
   - Certificate Chain 正常。

PowerShell 可先檢查：

```powershell
Get-ChildItem Cert:\LocalMachine\My |
    Select-Object Subject, Thumbprint, NotAfter, HasPrivateKey
```

### Phase A 驗收

- [ ] FQDN 已存在。
- [ ] DNS 正確指向 MCP Server。
- [ ] 公司憑證 SAN 與 FQDN 相符。
- [ ] MCP Server 可存取憑證 Private Key。
- [ ] MCP Client 所在機器信任公司 CA Chain。

---

# Phase B — 建立 Windows Service 常駐能力

**負責角色**：Coding Agent

**需要改程式 / Repository**：是

**建議方案**：WinSW

Python Process 本身不是 Windows Service。

不要把以下方式當作正式部署：

```text
登入 Windows
-> 開 PowerShell
-> 執行 Python
-> PowerShell 永遠不關
```

正式目標是 Windows Service。

## B1. 新增 Windows Deployment 目錄

建議新增：

```text
deploy/windows/
  MCPServer.xml
  install-service.ps1
  uninstall-service.ps1
  start-service.ps1
  stop-service.ps1
  restart-service.ps1
  README.md
```

WinSW Binary 建議固定名稱：

```text
deploy/windows/MCPServer.exe
```

離線正式機器不可執行安裝時才上網下載 WinSW。

## B2. Service 啟動命令

Windows Service 應直接使用 Project venv：

```text
<PROJECT_ROOT>\.venv\Scripts\python.exe
    -m mcp_server.server
    --transport streamable-http
```

Service Working Directory 必須明確設定為 Repository Root。

原因：目前部分功能仍會依賴 Project Root，例如：

```text
config.toml
scripts/generate_pptx.js
其他相對路徑資源
```

建議同時明確指定：

```text
WorkingDirectory=<PROJECT_ROOT>
MCP_CONFIG=<PROJECT_ROOT>\config.toml
```

## B3. Service Environment

Service 至少要取得：

```text
MCP_CONFIG=D:\...\MCP_server\config.toml
MCP_HTTP_BEARER_TOKEN=<secret>
MCP_LOG_LEVEL=INFO
MCP_LOG_FILE=D:\...\MCP_server\logs\mcp-server.log
```

注意：

```text
目前登入使用者的 User Environment Variables
```

不代表 Windows Service Account 一定能讀到。

不可假設現在 PowerShell 執行成功，轉成 Service 就一定成功。

## B4. Service Account

必須明確決定服務身份。

推薦順序：

1. 專用 Domain / Service Account。
2. 若不需要 Domain Network Identity，再評估 Local Service 類型帳號。

Service Account 必須有權限：

- 讀取 Project Directory。
- 讀取 `config.toml`。
- 執行 `.venv\Scripts\python.exe`。
- 寫入 Log Directory。
- 若啟用 Export，可寫入 Export Directory。
- 連線 PostgreSQL。
- 連線 MSSQL（若啟用）。
- 連線 Oracle（若啟用）。
- 存取 MCP `[filesystem].allowed_paths` 所需路徑。

不要直接使用高權限 Domain Admin 作為解決權限問題的方法。

## B5. Failure Recovery

Windows Service 必須設定 Crash Recovery。

目標：

```text
First failure      -> Restart Service
Second failure     -> Restart Service
Subsequent failure -> Restart Service
```

建議 Restart Delay：

```text
5 ~ 10 秒
```

避免 Crash Loop 每毫秒重新啟動。

## B6. Startup Type

目標：

```text
Startup Type = Automatic
```

若公司環境在 Windows 開機時網路或 Oracle 等資源較晚 Ready，可考慮：

```text
Automatic (Delayed Start)
```

## B7. Service Smoke Test

安裝後：

```powershell
Get-Service MCPServer
Start-Service MCPServer
Get-Service MCPServer
```

確認 Backend：

```powershell
Get-NetTCPConnection -LocalPort 8080 -State Listen
```

預期：

```text
LocalAddress = 127.0.0.1
```

接著：

1. 關閉所有手動開啟的 MCP PowerShell。
2. 登出目前 Windows User。
3. 再確認服務仍 Running。

### Phase B 驗收

- [ ] Windows 中存在 `MCPServer` Service。
- [ ] 不需要互動式登入即可啟動。
- [ ] PowerShell 關閉後服務不停止。
- [ ] Backend 只 Bind localhost。
- [ ] Log 可正常寫入。
- [ ] 強制結束 MCP Python Process 後會自動恢復。

---

# Phase C — 整合 Offline Package

**負責角色**：Coding Agent

**需要改 Repository**：是

需要檢查：

```text
scripts/pack_offline.ps1
scripts/install_offline.ps1
```

## C1. 將 WinSW 納入離線包

Online Build / Packaging Machine 可以取得公司核准版本的 WinSW。

但是最後產出的 Offline ZIP 必須包含完整 Service Deployment Asset。

正式 Windows Server 不應在：

```text
install-service.ps1
```

執行時再連 GitHub 或外網下載 WinSW。

## C2. 不要把 Runtime 安裝與 Service Registration 混在一起

保留明確職責：

```text
install_offline.ps1
    -> 建立 Python Runtime / venv / dependencies

install-service.ps1
    -> 註冊 Windows Service
```

這樣比較容易：

- Debug。
- Rollback。
- 更新 Python Dependency。
- 單獨重新註冊 Service。

## C3. 新 Windows 主機正式安裝順序

文件必須清楚記錄：

```text
1. 解壓 Offline Bundle
2. 執行 install_offline.ps1
3. 建立 / 修改 config.toml
4. 建立 Service Secret / Bearer Token
5. 設定 Service Account
6. 執行 deploy/windows/install-service.ps1
7. 驗證 localhost MCP HTTP
8. 設定 IIS HTTPS
9. 驗證跨機器 HTTPS MCP
```

### Phase C 驗收

- [ ] Fresh Offline Machine 不需 Internet 即可完成 Runtime 安裝。
- [ ] WinSW 已包含於 Offline Package。
- [ ] Windows Service Scripts 已包含於 Offline Package。
- [ ] 原本 Python Offline Install 流程沒有被破壞。

---

# Phase D — 正式 MCP HTTP 設定

**負責角色**：Coding / Deployment Agent

**需要改程式**：通常否

**需要改 config**：是

正式 `config.toml`：

```toml
[http]
host = "127.0.0.1"
port = 8080
bearer_token_env = "MCP_HTTP_BEARER_TOKEN"
```

## D1. allowed_hosts

必須先確認 IIS Reverse Proxy 最後傳進 MCP 的 Host Header。

理想模式是保留外部 Host。

設定例如：

```toml
allowed_hosts = [
    "127.0.0.1:*",
    "localhost:*",
    "<MCP_FQDN>:*",
]
```

若遇到：

```text
421 Misdirected Request
```

應先確認：

- IIS Preserve Host Header 行為。
- MCP 實際收到的 Host。
- `allowed_hosts` 是否包含正確 FQDN。

不要為了讓 421 消失直接設定無限制 wildcard 或關閉 DNS Rebinding Protection。

## D2. allowed_origins

一般非瀏覽器 MCP Client 可能不會帶 `Origin`。

如果後續有 Browser-based Client，才把實際可信 Origin 加入：

```toml
allowed_origins = [
    "https://<trusted-ui-host>"
]
```

不要預設使用：

```text
*
```

## D3. Bearer Token 儲存

Production Token 不可放進：

```text
GitHub
config.toml
README
install script
log
```

`config.toml` 只保留 Environment Variable Name：

```toml
bearer_token_env = "MCP_HTTP_BEARER_TOKEN"
```

真正 Token 由 Service Runtime 取得。

Token 可透過 Project Python 產生：

```powershell
& ".\.venv\Scripts\python.exe" -c "import secrets; print(secrets.token_urlsafe(32))"
```

正式部署後要另外記錄 Token Rotation 方法。

### Phase D 驗收

- [ ] MCP 只監聽 `127.0.0.1:8080`。
- [ ] 遠端 Client 無法直接使用 Port 8080。
- [ ] Service Process 能取得 `MCP_HTTP_BEARER_TOKEN`。
- [ ] `allowed_hosts` 包含正式 FQDN。

---

# Phase E — 安裝及設定 IIS Reverse Proxy + 公司 HTTPS

**負責角色**：Windows / IIS Administrator / Deployment Agent

**需要改 Python**：否

## E1. IIS 必要元件

通常需要：

- IIS Web Server
- URL Rewrite Module
- Application Request Routing（ARR）
- ARR Proxy Enabled

正式機器若無法上網，需要事先透過公司核准方式取得 Offline Installer。

不要預設 Windows Server 一定已經有 URL Rewrite 與 ARR。

## E2. 啟用 ARR Proxy

IIS Manager：

```text
Server
  -> Application Request Routing Cache
  -> Server Proxy Settings
  -> Enable proxy
```

不同 ARR 版本 UI 名稱可能略有差異。

## E3. 建立 MCP IIS Site

建議：

```text
Site Name: MCPServer
Binding Type: https
Port: 443
Host Name: <MCP_FQDN>
Certificate: 公司簽發給 <MCP_FQDN> 的憑證
```

除非公司 Network Policy 有要求，不建議正式 Endpoint 使用任意高 Port HTTPS。

## E4. HTTPS Binding

憑證應由：

```text
Local Computer / Personal
```

Certificate Store 直接提供 IIS 使用。

確認：

- FQDN Match。
- Certificate 未過期。
- 有 Private Key。
- Chain Trusted。

Private Key 不需要因 MCP 而匯出成 `.key`。

## E5. `/mcp` Reverse Proxy

正式 Routing：

```text
https://<MCP_FQDN>/mcp
    -> http://127.0.0.1:8080/mcp
```

Reverse Proxy 必須保留：

- HTTP Method。
- Query String。
- `Authorization` Header。
- Streamable HTTP 所需 Streaming 行為。
- 正確 Host Header 行為。

不要設定會把 MCP Bearer Header 吃掉或改寫掉的 IIS Authentication Flow。

推薦 IIS MCP Proxy Layer：

```text
Anonymous Authentication: Enabled
Windows Authentication: Disabled
Basic Authentication: Disabled
```

實際 MCP Authentication 仍由：

```text
BearerTokenMiddleware
```

負責。

## E6. Proxy Timeout

部分 MCP Tool Call 可能需要較長時間，例如：

- Database Query。
- GMS History。
- Oracle Aggregation。
- 大型資料處理。

因此 IIS / ARR Proxy Timeout 必須高於合理 MCP Request Window。

不要因 IIS Timeout 過短，導致 Backend 還在執行但 Proxy 先切斷。

同時不可移除 Oracle `call_timeout`。

兩者解決不同問題：

```text
IIS Timeout
    -> Reverse Proxy 層

Oracle call_timeout
    -> Database Round-trip 層
```

## E7. Firewall

對外主要開放：

```text
TCP 443 -> IIS
```

Backend：

```text
127.0.0.1:8080
127.0.0.1:8081  # 若啟用 Export
```

不應要求 LAN Client 直接存取 Backend Port。

### Phase E 驗收

- [ ] IIS HTTPS Binding 正常。
- [ ] Client 收到正確公司憑證。
- [ ] `/mcp` 正確 Proxy 至 localhost MCP。
- [ ] `Authorization` Header 能完整送到 MCP。
- [ ] 無 Token 回 401。
- [ ] 錯誤 Token 回 401。
- [ ] 正確 Token 能建立 MCP Session。

---

# Phase F — 將 Export Download 一併 HTTPS 化

**負責角色**：Coding Agent + IIS Administrator

**需要改程式**：若 `serve_downloads=true`，是

若正式環境確定：

```toml
serve_downloads = false
```

本 Phase 可標示 Not Applicable。

## F1. 現有問題

目前 `download_server.py` 會產生類似：

```text
http://<advertise_host>:8081/exports/<id>
```

但是正式架構應回傳：

```text
https://<MCP_FQDN>/exports/<id>
```

Backend Download Server 仍可維持 localhost HTTP。

## F2. 新增 Public Base URL 設定

推薦新增：

```toml
[export]
serve_downloads = true
public_base_url = "https://<MCP_FQDN>/exports"
download_host = "127.0.0.1"
download_port = 8081
```

`register_file()` 應改成依 `public_base_url` 回傳：

```text
{public_base_url}/{file_id}
```

不可再從 Backend Bind Address 推算 Public URL。

原因：

```text
Backend address != Client reachable address
```

在 Reverse Proxy 架構下必須明確區分。

## F3. Backward Compatibility

若目前已有環境使用：

```text
advertise_host
download_port
```

則不要不經評估直接 Breaking Change。

推薦：

```text
1. 若 public_base_url 有設定 -> 使用 public_base_url
2. 否則 -> 保留舊 advertise_host + download_port 邏輯
```

並在 Startup Validation 檢查：

- URL 結構合法。
- 不應產生重複 Slash。
- 不應包含 Credentials。
- 正式範例必須使用 HTTPS。

## F4. IIS `/exports` Proxy

Routing：

```text
https://<MCP_FQDN>/exports/*
    -> http://127.0.0.1:8081/exports/*
```

原有：

```text
unguessable file id
TTL
expiry
```

語意應保持不變。

若未來要求 Export 也使用 Bearer Authentication，應另立需求設計，不要在 HTTPS Migration 過程中意外破壞既有 Cross-machine Handoff。

## F5. Unit Test

至少新增：

- `public_base_url` Parsing。
- HTTPS URL Generation。
- Slash Normalization。
- Fallback Behavior。
- Invalid Public URL Validation。

### Phase F 驗收

- [ ] Export Server 只 Bind localhost。
- [ ] MCP 回傳 URL 為 `https://<MCP_FQDN>/exports/...`。
- [ ] 遠端 Server 可經 IIS 下載檔案。
- [ ] Client 不需直接連 Port 8081。

---

# Phase G — Logging、Recovery 與正式運維驗證

**負責角色**：Coding / Deployment Agent

## G1. Persistent Logging

設定：

```text
MCP_LOG_LEVEL=INFO
MCP_LOG_FILE=<LOG_DIRECTORY>\mcp-server.log
```

確認：

- Service Account 可寫入。
- Logout 後 Log 仍持續寫入。
- Token 不會出現在 Log。
- DB Password 不會出現在 Log。
- API Key 不會出現在 Log。

## G2. Process Crash Recovery Test

在維護時段主動結束 Service 內的 Python Process。

預期：

```text
Python Process Exit
-> WinSW / Windows Service 偵測失敗
-> Restart
-> 127.0.0.1:8080 再次 Listen
```

## G3. Windows Reboot Test

重開 Windows Server。

在沒有手動登入執行 MCP 的情況下：

```powershell
Get-Service MCPServer
Get-NetTCPConnection -LocalPort 8080 -State Listen
```

預期：

```text
Status = Running
LocalAddress = 127.0.0.1
```

再從其他 Client 測 HTTPS。

## G4. Database Regression

使用正式 Service Account 執行代表性 Tool。

依啟用功能至少測：

```text
db_list_databases
db_query
gms_list_points
gms_realtime_values
gms_history_aggregate
```

確認從 Interactive User 切到 Service Account 後，沒有造成：

- Network Permission 問題。
- Oracle Auth 問題。
- PostgreSQL Auth 問題。
- File Share Permission 問題。

## G5. Connection Pool Shutdown Regression

目前 Server 正常結束時已有 DB Pool Cleanup。

新增 Windows Service 後不可破壞此行為。

需測試：

```text
Start Service
-> 執行 DB Tool
-> Stop Service
-> 檢查 DB Connection
```

以及：

```text
Restart Service 多次
```

確認沒有明顯 Connection 不斷累積。

### Phase G 驗收

- [ ] Persistent Log 正常。
- [ ] Crash 後 Service 自動恢復。
- [ ] Reboot 後 Service 自動恢復。
- [ ] Database Tools 可用。
- [ ] GMS Tools 可用。
- [ ] Service Restart 沒有明顯 Connection Leak。

---

# Phase H — 跨機器 End-to-End Security Test

**負責角色**：Deployment Agent / MCP Client Owner

一定要從另一台機器測試，不能只測 localhost。

## H1. DNS

```powershell
Resolve-DnsName <MCP_FQDN>
```

必須解析到正確 MCP Server。

## H2. TLS

Client 連：

```text
https://<MCP_FQDN>/mcp
```

TLS 必須在沒有以下設定時成功：

```text
verify=False
ignore certificate
skip SSL verification
```

如果失敗，依序檢查：

1. Root CA。
2. Intermediate CA。
3. SAN。
4. Certificate Expiry。
5. Client / Server System Clock。

## H3. Bearer Negative Test

沒有 Token：

```text
Expected: HTTP 401 Unauthorized
```

錯誤 Token：

```text
Expected: HTTP 401 Unauthorized
```

## H4. Bearer Positive Test

使用與 MCP Service 相同 Token：

```text
Expected: MCP initialize / session request 成功
```

不要只使用瀏覽器 GET `/mcp` 判斷 MCP 可用。

必須使用：

```text
MCP Client
或 Protocol-valid MCP Request
```

## H5. Host Header / 421 Test

若看到：

```text
421 Misdirected Request
```

依序：

1. 確認 MCP 收到的 Host。
2. 確認 IIS 是否 Preserve Host。
3. 檢查 `[http].allowed_hosts`。
4. 加入精確 FQDN / Port Pattern。

不要直接關掉 DNS Rebinding Protection。

## H6. Export Test

若啟用 Export：

1. 執行會回傳 `download_url` 的 Tool。
2. URL 必須為：

```text
https://<MCP_FQDN>/exports/...
```

3. 從 Consumer Machine 下載。
4. 確認 TTL 到期後失效。
5. 確認不需直接連：

```text
http://server:8081
```

### Phase H 驗收

- [ ] Remote Client TLS 驗證成功。
- [ ] Missing Token Fail。
- [ ] Wrong Token Fail。
- [ ] Correct Token Success。
- [ ] MCP Tool End-to-End 成功。
- [ ] Export HTTPS 成功（若啟用）。

---

# Phase I — 文件與正式操作手冊

**負責角色**：Documentation / Deployment Agent

實作完成後必須同步更新：

```text
README.md
config.toml.example
deploy/windows/README.md
docs/HANDOFF.md
```

以及本文件狀態。

## I1. README

README 必須明確寫出正式架構：

```text
Corporate HTTPS
-> IIS
-> localhost MCP Windows Service
```

並區分：

```text
手動 python 啟動 = Development / Debug
Windows Service = Production
```

## I2. config.toml.example

若 Phase F 完成，加入：

```toml
public_base_url = "https://mcp.internal.example.com/exports"
```

只能放範例，不可放正式 Secret。

## I3. Windows Service Runbook

至少記錄：

```powershell
Get-Service MCPServer
Start-Service MCPServer
Stop-Service MCPServer
Restart-Service MCPServer
```

以及：

- Service Name。
- Service Account。
- Project Root。
- Config Path。
- Log Path。
- 更新程式方式。
- 哪些情況需要重新執行 `install_offline.ps1`。
- Bearer Token Rotation。
- IIS Certificate Renewal / Replacement。
- Service Uninstall。

## I4. 正式升級流程

標準流程：

```text
1. Stop MCPServer
2. 備份 config.toml 與正式環境設定
3. 更新 Repository / 解壓新版 Offline Bundle
4. 若 Dependency 有變更，重新執行 install_offline.ps1
5. Start MCPServer
6. 檢查 Log
7. localhost Smoke Test
8. HTTPS Smoke Test
9. MCP Tool Regression Test
```

若只是 Python Source Code 變更且 Dependency 沒變，不應強迫整個 venv 重建。

### Phase I 驗收

- [ ] 新維護者不需要原作者即可部署。
- [ ] Start/Stop/Restart 流程清楚。
- [ ] Certificate Renewal 有文件。
- [ ] Bearer Token Rotation 有文件。
- [ ] Offline Upgrade 有文件。

---

# 4. 建議執行順序

後續 Agent 預設依照以下順序：

```text
1. Phase A — 收集 FQDN / Certificate / Service Account
2. Phase B — Windows Service
3. Phase C — Offline Package
4. Phase D — localhost Production Config
5. 驗證 MCP 作為 Windows Service 可正常跑 localhost HTTP
6. Phase E — IIS + Corporate HTTPS
7. 驗證 HTTPS /mcp + Bearer Token
8. Phase F — HTTPS Export（若啟用）
9. Phase G — Recovery / Reboot / DB Regression
10. Phase H — Remote Client End-to-End Test
11. Phase I — Final Runbook / Documentation
```

重要原則：

> 不要同時 Debug Windows Service、IIS、TLS、Bearer Token、Database Permission 五個層級。
>
> 第一階段先證明 MCP 能作為 Windows Service 在 localhost 穩定運行；第二階段才加入 IIS / TLS；第三階段才測跨機器 Agent。

---

# 5. 不同 Agent 的工作邊界

這一節用來避免多人或多 Agent 接手時需求發散。

## Agent 1 — Windows Service 實作

主要 Scope：

```text
deploy/windows/**
scripts/pack_offline.ps1
scripts/install_offline.ps1
README 中必要的部署引用
```

不可順便重新設計：

- GMS Tool。
- Database Tool。
- MCP Schema。
- Query 行為。

交付物：

- WinSW Service Definition。
- Install / Uninstall / Start / Stop / Restart Scripts。
- Offline Packaging Integration。
- Service Lifecycle Test。

## Agent 2 — IIS / 公司憑證部署

主要 Scope：

```text
deploy/windows/README.md
docs/** 部署相關內容
config.toml.example 註解
```

主要工作可能是 Windows / IIS Configuration，而不是 Python。

交付物：

- IIS Reverse Proxy 設定。
- HTTPS Binding。
- 公司憑證驗證。
- Host / Authorization Header 驗證。
- Firewall 設定。
- 實際 Acceptance Test 結果。

## Agent 3 — HTTPS Export URL

主要 Scope：

```text
src/mcp_server/config.py
src/mcp_server/utils/download_server.py
config.toml.example
tests/** export/config 相關測試
```

不可破壞：

- Export ID Randomness。
- TTL。
- Expiration。
- Cross-machine handoff 語意。

交付物：

- `public_base_url` 或等價機制。
- Backward Compatibility Decision。
- Unit Test。
- HTTPS `download_url`。

## Agent 4 — E2E Validation / Final Documentation

主要 Scope：

```text
tests/**
README.md
docs/HANDOFF.md
deploy/windows/README.md
本文件
```

交付物：

- Test Matrix。
- Reboot Verification。
- Crash Recovery Verification。
- Remote MCP Client Verification。
- Final Operator Runbook。

---

# 6. Agent 交接規範

每個 Agent 完成工作後，必須更新本文件，避免下一個 Agent 重新調查一次。

## 6.1 Phase 狀態

每個 Phase 僅使用以下狀態：

```text
NOT_STARTED   = 尚未開始
IN_PROGRESS   = 執行中
BLOCKED       = 有阻塞
CODE_COMPLETE = 程式已完成但尚未正式環境驗證
VERIFIED      = 已完成且驗證通過
NOT_APPLICABLE = 此環境不需要
```

## 6.2 目前狀態

| Phase | 狀態 | Owner / Agent | Evidence / Notes |
|---|---|---|---|
| A 基礎環境資訊 | NOT_STARTED | — | 需要正式 FQDN、憑證、Service Account、路徑 |
| B Windows Service | NOT_STARTED | — | Repo 目前只有 Linux systemd |
| C Offline Packaging | NOT_STARTED | — | WinSW 尚未整合 Offline Bundle |
| D Production HTTP Config | NOT_STARTED | — | 需依正式 FQDN / IIS Host 行為完成 |
| E IIS + 公司 HTTPS | NOT_STARTED | — | 已知使用公司憑證，但實際 Thumbprint / FQDN 尚未記錄 |
| F HTTPS Export | NOT_STARTED | — | `serve_downloads=true` 時需要 |
| G Operations / Recovery | NOT_STARTED | — | 需先完成 Windows Service |
| H End-to-End Test | NOT_STARTED | — | 需 IIS 與 Client CA Trust |
| I Final Runbook | NOT_STARTED | — | 正式驗證後完成 |

## 6.3 每次 Handoff 必填格式

每個 Agent 在本文件底部新增：

```markdown
## Handoff YYYY-MM-DD — <Agent 名稱>

### 已完成
- ...

### 修改檔案
- `path/to/file`

### 已執行驗證
- command / result

### 尚未完成
- ...

### Blocker
- 無 / 明確阻塞原因

### 重要決策
- ...

### 下一步
- ...
```

不要只寫：

```text
服務已更新
HTTPS 已完成
```

必須包含：

- 檔名。
- Command。
- Result。
- Blocker。
- 下一步。

---

# 7. Security Guardrails

以下為硬性限制：

- 不可 Commit 公司憑證 Private Key。
- 不可 Commit 含 Private Key 的 `.pfx`。
- 不可 Commit Production Bearer Token。
- 不可把 Bearer Token 寫進一般 Application Log。
- 不可把 DB Password / API Key 寫入 Log。
- 不可以關閉 TLS Verification 當作正式解法。
- 不可因已啟用 HTTPS 就移除 Bearer Authentication。
- IIS 上線後不可讓 Backend `8080` 對整個 LAN 開放。
- IIS 上線後不可讓 Export `8081` 對整個 LAN 開放。
- 不可為了解決 421 就直接關閉 DNS Rebinding Protection。
- 不可以過度權限的 Admin Account 當 Service Account 來迴避權限問題。
- 必須保留 Database Alias / Server-side Secret 架構。
- 必須保留 Oracle Read-only 保護。
- 必須保留 Database Connection Pool 限制與 Cleanup。

---

# 8. Rollback Plan

部署失敗時必須能回復，不應邊修邊破壞目前可用環境。

## 8.1 Windows Service Rollback

```text
1. Stop MCPServer
2. Uninstall MCPServer Service
3. 不自動刪除 config.toml
4. 不自動刪除 logs
5. 回到 PowerShell 手動啟動做 Debug
```

Debug Fallback：

```powershell
Set-Location <PROJECT_ROOT>
& ".\.venv\Scripts\python.exe" -m mcp_server.server --transport streamable-http
```

此方式只作 Troubleshooting，不代表 Production 已完成。

## 8.2 IIS Rollback

```text
1. Disable / Remove MCP Rewrite Rule
2. 保留 Certificate 在 Windows Certificate Store
3. Backend 優先仍維持 localhost
4. 從 localhost 先確認 MCP 正常
5. 再重新導入 IIS
```

不要把：

```text
重新對全 LAN 開放 8080
```

當作預設 Rollback。

---

# 9. Troubleshooting Matrix

| 症狀 | 可能區域 | 第一優先檢查 |
|---|---|---|
| PowerShell 關閉後 MCP 停止 | Windows Service 尚未完成 | `Get-Service MCPServer` |
| Service 一啟動就停止 | Working Directory / Env / Config | WinSW Log、`MCP_CONFIG`、Python Path |
| 手動執行正常，Service 不正常 | Service Account 權限 / Env | DB、Network、Filesystem、Bearer Env |
| HTTPS 出現憑證警告 | CA / SAN / Chain | Root CA、Intermediate、SAN、Expiry |
| IIS 後變 401 | Bearer Token / Header | `Authorization` Forward、Service Env |
| 出現 421 | Host / allowed_hosts | IIS Preserve Host、`allowed_hosts` |
| IIS 回 502 | Backend 沒啟動 | Service Status、localhost:8080 |
| 長 Query 中途斷線 | ARR Timeout | Proxy Timeout + App/DB Timeout |
| Export URL 還是 `http://...:8081` | URL Generator | Phase F `public_base_url` |
| Export HTTPS 404 | IIS Rule / TTL | `/exports` Rewrite、ID 是否過期 |
| DB 手動執行正常但 Service 失敗 | Service Identity | Service Account DB / Network 權限 |
| Stop Service 後 DB Session 長時間不消失 | Lifecycle / Pool Cleanup | Shutdown Path、Pool Cleanup Log |

---

# 10. 最終驗證紀錄

未填完以下實際結果前，不得把整份計畫標示為完成。

| 測試 | Expected | Actual | Pass / Fail | Date |
|---|---|---|---|---|
| Windows Service Install | `MCPServer` 存在 | — | — | — |
| 關閉 PowerShell | MCP 繼續運作 | — | — | — |
| Backend Listener | `127.0.0.1:8080` | — | — | — |
| Windows Reboot | Service 自動啟動 | — | — | — |
| Corporate TLS | Trust 正常且 SAN Match | — | — | — |
| HTTPS `/mcp` | 成功到 MCP | — | — | — |
| Missing Bearer | 401 | — | — | — |
| Wrong Bearer | 401 | — | — | — |
| Correct Bearer | MCP Success | — | — | — |
| Remote 8080 | 無法直接存取 | — | — | — |
| Export HTTPS | 成功（若啟用） | — | — | — |
| DB Smoke Test | Success | — | — | — |
| GMS Smoke Test | Success | — | — | — |
| Crash Recovery | 自動 Restart | — | — | — |
| Connection Cleanup | 無明顯 Pool Leak | — | — | — |

---

# 11. 目前立即下一步

第一個實作 Agent 應從：

```text
Phase A + Phase B
```

開始，而不是直接先改 IIS。

原因：

在加入 Reverse Proxy、Certificate、FQDN、TLS 之前，先證明以下條件：

```text
MCP 可以在沒有 PowerShell 的情況下運行
Service Environment 正確
Service Account 權限正確
Database 連線正常
Bearer Token 正常
Persistent Log 正常
Crash 可以恢復
```

第一個實際 Code Change 應為：

```text
新增 deploy/windows/ 的 WinSW Windows Service Deployment
+ 將所需 Asset 整合進 Offline Packaging
```

完成 localhost Windows Service 驗證後，再進入 IIS + 公司 HTTPS。

---

# Handoff Log

## Handoff 2026-08-20 — 初始正式部署計畫

### 已完成

- 檢查目前 MCP Streamable HTTP Runtime。
- 檢查 HTTP Config。
- 確認 Repo 目前只有 Linux systemd Deployment。
- 檢查目前 Export Download Server 為 HTTP。
- 確立正式 Target Architecture：IIS 使用公司 CA 憑證，Reverse Proxy 至 localhost MCP，MCP 改由 Windows Service 常駐。
- 定義 Export HTTPS 化方案。
- 定義 Agent 工作邊界。
- 定義 Acceptance Criteria、Rollback、Troubleshooting Matrix 與 Final Validation Matrix。
- 本文件已完整改為中文，保留必要的程式名稱、設定名稱、Command 與 Protocol 英文原名，避免後續 Agent 誤解實際識別字。

### 修改檔案

- `docs/WINDOWS_HTTPS_DEPLOYMENT_PLAN.md`

### 已執行驗證

- 已確認 Repository 相關程式與部署結構。
- 尚未修改正式 Windows Server。
- 尚未進行 IIS / Certificate 實機配置。

### 尚未完成

- Phase A ~ I 實作與實機驗證。

### Blocker

目前尚未在文件記錄以下正式值：

```text
MCP_FQDN
Certificate Thumbprint
Service Account
Production Project Root
正式 Log Path
```

### 重要決策

- 公司 TLS 憑證由 IIS 管理。
- Uvicorn / MCP Backend 維持 localhost HTTP。
- Bearer Token 必須保留。
- Windows Service 是 Production Requirement。
- PowerShell 手動執行只保留作 Development / Troubleshooting。
- 若使用 Export，對外 `download_url` 也必須改為 HTTPS。

### 下一步

執行 Phase B Windows Service 與 Phase C Offline Packaging，先完成 localhost Service 驗證，再開始 IIS + 公司 HTTPS。
