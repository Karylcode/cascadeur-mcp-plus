[English](troubleshooting.md) | **繁體中文** · [文件索引](README.zh-TW.md)

# 疑難排解

## 提示和已知限制

- **8765 被佔用** → *Start script server* 會出現 `WinError 10013`，代表有其他程式用了這個連接埠。
  請把那個程式移到別的埠（`netstat -ano | findstr :8765` 可以查出是誰）。
- **視窗最小化** → 畫面不會繪製，`screenshot` 會回傳錯誤。截圖請加上 `restore=true`。
- **截圖全黑** → `screenshot` 會附上警告。視窗可能被全螢幕程式蓋住，或還沒開始繪製。
- **拖曳用程式擺好的姿勢** → Cascadeur 可能跳出「Pose in the current frame is different from the AutoPosing
  prediction…」。`ui_drag` 會在 `dialogs` 裡回報，選 *Yes* 保留姿勢後再拖一次。
- **視埠投影**假設是透視相機、垂直視角 30°（在 2026.2.3 量測）。設定不同時，請在 `project_point`
  或 `ui_drag` 傳入 `fov_deg`。其他類型的相機會被拒絕。
- **長時間的操作** → 呼叫在 Cascadeur 的主執行緒執行，長時間的操作會讓介面暫停回應，直到完成。
- **Cascadeur 改版** → 介面結構可能改變，盡量用 `actionId` 或按鈕文字定位，少用座標。
- **找不到動作** → `list_actions` 讀的是目前的 QML 介面樹，從沒開過的工具，它的動作可能不會出現。
- **未知的 ref** → 每次呼叫 `ui_tree` 或 `dialogs`，ref 都會重設。請重新呼叫 `ui_tree`。

## 錯誤訊息

| 訊息開頭 | 處理方式 |
|---|---|
| `Cascadeur MCP Plus is not reachable and could not be started` | 開啟 Cascadeur，點 **Scripts → MCP → Start script server**，再試一次。 |
| `… is used by another program, not …` | `48766` 或 `8765` 被其他程式佔用，沒有送任何東西過去。請釋放連接埠或移走那個程式，再試一次。請看[安全](safety.zh-TW.md)。 |
| `MCP Plus start script ran but the server did not come up` | 查看 Cascadeur 的日誌檔（路徑寫在[工具](tools.zh-TW.md)的 `get_log`）。 |
| `Cascadeur rejected the MCP Plus start script` | script server 回傳錯誤，訊息裡附有它的回應。 |
| `No response from Cascadeur within …` | Cascadeur 可能正在忙或已經凍結。 |
| `Cascadeur did not finish within …` | 主執行緒工作逾時，介面可能正在忙。如果是 `run_python`，請加大 `timeout`（最多 600）。 |
| `Lost connection to Cascadeur MCP Plus` | Cascadeur 可能已經關閉或當機。 |
| `Refused to click …` | Quick Rigging 防護。請先呼叫 `enter_rig_mode`。請看[安全](safety.zh-TW.md)。 |
