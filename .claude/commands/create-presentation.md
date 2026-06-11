# 簡報製作流程

依照以下步驟引導使用者完成簡報製作。每個步驟都要等使用者確認後再繼續。

## 步驟 1：確認主題與用途

詢問使用者：
- 這份簡報的主題是什麼？
- 預計給誰看（內部團隊 / 客戶 / 高層 / 培訓）？
- 大約需要幾頁（建議 8-15 頁）？

根據用途建議 deck_type：
- 內部報告 / 專案進度 → `project_status`
- 產品展示 / 提案 → `product_pitch`
- 技術說明 / 架構 → `technical`
- 教育訓練 → `training`
- 其他 → `general`

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

## 步驟 4：填入實際內容

依照架構每頁填入真實內容，遵守以下規則：
- 每頁標題 = 完整論點，不是標籤（例：「批次寫入將吞吐量提升 8 倍」而非「效能」）
- 每個 bullet = 完整句子（8-20 字），不是 1-2 字標籤
- content 頁：4-6 個 bullet
- stats 頁：2-4 個 KPI 卡片，搭配 icon
- 每頁加上 speaker notes

加入 icon 提升視覺（可用名稱：check x arrow-right database cloud server network cpu trending-up shield zap rocket lightbulb users target briefcase calendar settings star）：
- section 頁：`"icon": "server"` 等
- stats 卡片：`"icon": "trending-up"` 等
- two_column：`"left_icon": "check", "right_icon": "x"`

## 步驟 5：確認輸出路徑後產出

詢問使用者輸出路徑（例如 `D:/FAC_Job/output.pptx`），然後呼叫：
```
create_presentation(slides_json="...", output_path="...")
```

產出完成後，告知使用者：
- 檔案路徑與大小
- 如有 CONTENT QUALITY NOTES 警告，逐條說明並詢問是否需要修改
