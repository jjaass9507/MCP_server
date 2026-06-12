# 簡報製作流程

依照以下步驟引導使用者完成簡報製作。每個步驟都要等使用者確認後再繼續。

## 步驟 1：確認主題與用途

詢問使用者：
- 這份簡報的主題是什麼？
- 預計給誰看（內部團隊 / 客戶 / 高層 / 培訓）？
- 大約需要幾頁？

根據用途建議 deck_type 與頁數：

| deck_type | 建議頁數 | 適用場景 |
|-----------|---------|---------|
| `project_status` | 7-9 頁 | 內部報告、專案進度 |
| `product_pitch`  | 8-11 頁 | 產品展示、投資提案 |
| `technical`      | 9-12 頁 | 技術說明、架構設計 |
| `training`       | 8-10 頁 | 教育訓練、教材 |
| `general`        | 8-12 頁 | 其他用途 |

如果使用者指定的頁數低於建議值，告知哪些主題會被合併，讓使用者確認後再繼續。

## 步驟 2：選擇視覺風格

呼叫 `list_presentation_styles()` 取得完整 preset 清單後，向使用者介紹選項：

| Preset | 適合場合 |
|--------|---------|
| `corporate` | 正式商務、法說會 |
| `bright_sans` | 簡潔現代、Google 風格 |
| `aurora` | 高端深色、科技感 |
| `warm` | 友善活潑、橘色調 |
| `dark` | 暗色科技、開發者 |
| `minimal` | 極簡白底 |
| `modern` | 紅色強調 |
| `tech` | 紫色深色 |

詢問使用者：
- 想要哪個 preset？
- 是否需要中文字型（Microsoft JhengHei / Microsoft YaHei）？
- 是否要在每頁顯示頁碼和章節名稱（show_footer）？

## 步驟 3：規劃每頁內容架構

用確認好的參數呼叫：
```
plan_presentation_outline(topic="...", slide_count=N, deck_type="...")
```

把回傳的逐頁架構完整呈現給使用者，讓他確認或調整。
如果回傳包含 `NOTE:` 壓縮說明，逐條說明哪些主題被合併，詢問是否要增加頁數。

## 步驟 4：填入實際內容

依照架構每頁填入真實內容，遵守以下規則：
- 每頁標題 = 完整論點，不是標籤（例：「批次寫入將吞吐量提升 8 倍」而非「效能」）
- 每個 bullet = 完整句子（8-20 字），不是 1-2 字標籤
- content 頁：4-6 個 bullet
- stats 頁：2-4 個 KPI 卡片，搭配 icon
- 每頁加上 speaker notes

加入 icon 提升視覺（可用名稱：check x arrow-right database cloud server network cpu trending-up shield zap rocket lightbulb users target briefcase calendar settings star）：
- section 頁：`"icon": "server"` 等
- big_message 頁：`"icon": "rocket"` 等（可選）
- stats 卡片：`"icon": "trending-up"` 等
- two_column：`"left_icon": "check", "right_icon": "x"`
- timeline 事件：`"icon": "calendar"` 等（可選）
- process 步驟：`"icon": "settings"` 等（可選）

### 新版型填寫指引

**`agenda`** — 目錄頁：
```json
{ "layout": "agenda", "title": "今日議程",
  "items": ["章節一：背景", "章節二：解決方案", "章節三：數據"],
  "active_item": 1 }
```
- items = 章節名稱清單（4-6 項）
- active_item（選填）= 當前章節的序號（用於在章節間重複目錄頁時高亮）

**`big_message`** — 大字訴求頁：
```json
{ "layout": "big_message", "message": "核心訊息（15字內）",
  "supporting": "1-2句說明文字", "icon": "rocket" }
```
- 預設使用 accent 色背景，強烈視覺衝擊
- 加 `"bg": "subtle"` 改用淡雅白底

**`timeline`** — 時間軸頁：
```json
{ "layout": "timeline", "title": "產品藍圖",
  "events": [
    { "date": "Q1 2026", "label": "里程碑名稱", "desc": "說明文字", "icon": "rocket" },
    { "date": "Q2 2026", "label": "下一步",     "desc": "說明文字" }
  ] }
```
- events 陣列，每項含 date / label / desc，icon 選填
- 3-6 個事件最佳

**`process`** — 流程步驟頁：
```json
{ "layout": "process", "title": "部署流程",
  "steps": [
    { "label": "環境準備", "desc": "安裝依賴、確認設定", "icon": "settings" },
    { "label": "部署執行", "desc": "執行安裝腳本",       "icon": "terminal" },
    { "label": "驗證測試", "desc": "確認服務正常運作",    "icon": "check" }
  ] }
```
- 3-5 個步驟最佳，步驟間自動加箭頭

## 步驟 5：確認輸出路徑後產出

詢問使用者輸出路徑（例如 `D:/FAC_Job/output.pptx`），然後呼叫：
```
create_presentation(slides_json="...", output_path="...")
```

產出完成後，告知使用者：
- 檔案路徑與大小
- 如有 CONTENT QUALITY NOTES 警告，逐條說明並詢問是否需要修改
