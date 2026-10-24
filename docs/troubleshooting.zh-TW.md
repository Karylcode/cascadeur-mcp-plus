[English](troubleshooting.md) | **繁體中文** · [文件索引](README.zh-TW.md)

# 疑難排解

請先在 checkout 資料夾裡執行 `python -m cascadeur_mcp_plus_cli doctor`。它會回報 Cascadeur 的安裝、連接埠、伺服器和 MCP 用戶端，而且只有在發現真正的問題時，才會以結束代碼 1 結束。要查看 Cascadeur 內的 Python traceback 和其他錯誤，請呼叫 `get_log`，或讀取 `CASCADEUR_LOG` 指定的檔案。

## 常見問題

- **Start script server 失敗，出現 `WinError 10013`。** 有其他程式佔用了 8765 連接埠。用 `netstat -ano | findstr :8765` 找出是誰，再把它移到別的連接埠。
- **渲染被拒絕，或結果是空的。** Cascadeur 最小化了。請用 `screenshot` 加上 `restore=true` 還原視窗，再試一次。
- **截圖全黑。** `screenshot` 會附上警告。視窗可能被全螢幕程式蓋住，或是還沒開始繪製。
- **「Unavailable in this Cascadeur version」。** 請呼叫 `get_status`。它會列出每個工具缺少的 API 或動作。
- **拖曳姿勢後出現提示。** Cascadeur 可能顯示「Pose in the current frame is different from the AutoPosing prediction」。`ui_drag` 會在 `dialogs` 裡回報它。請回答 *Yes* 保留姿勢，然後再拖曳一次。
- **視埠投影有偏差。** `project_point` 和 `ui_drag` 假設是垂直視角 30° 的透視攝影機，這個數值是在 2026.2.3 上量測的。如果你的設定不同，請傳入 `fov_deg`。其他類型的攝影機會被拒絕。
- **長時間的操作會讓 Cascadeur 凍結。** 呼叫會在 Cascadeur 的 GUI 執行緒上執行，所以長時間的操作會讓它的介面凍結，直到操作結束。請以短間隔輪詢 `get_job`，不要一次長時間等待。
- **介面工具不再找得到元素。** QML 版面配置可能隨 Cascadeur 的版本而改變。請優先使用 `actionId` 或畫面上可見的文字，不要用座標。
- **`list_actions` 少了某個動作。** 它讀取的是即時的 QML 介面樹，所以從未開啟過的工具，它們的動作可能不會出現。
- **未知的元素參照。** 每次呼叫 `ui_tree` 或 `dialogs`，元素參照都會重設。請再呼叫一次 `ui_tree`。
- **找不到控制器名稱。** 請用 `list_controllers` 查看角色的名稱和別名。未知的名稱會回傳 `closest` 建議。
- **`video_mocap` 被拒絕。** 模型檔 `mocap.pt` 還沒有安裝。請看[行為與限制](limitations.zh-TW.md#重定向綁定和動捕)。
- **更新後缺少工具。** 請重新啟動 MCP 用戶端。如果指令路徑變了，請再執行一次 `setup`；套件更新後，請再執行一次 `autostart enable`。

## 錯誤訊息

| 訊息開頭 | 處理方式 |
|---|---|
| `Cascadeur MCP Plus is not reachable and could not be started` | 開啟 Cascadeur，點 **Scripts → MCP → Start script server**，再試一次。 |
| `… is used by another program, not …` | 有其他程式佔用 48766 或 8765 連接埠，而且沒有送出任何東西給它。請釋放該連接埠或移走那個程式，再試一次。請看[安全](safety.zh-TW.md)。 |
| `MCP Plus start script ran but the server did not come up` | 查看 Cascadeur 的日誌檔。 |
| `Cascadeur rejected the MCP Plus start script` | script server 回傳了錯誤。訊息裡附有它的回應。 |
| `No response from Cascadeur within …` | Cascadeur 可能正在忙，或已經凍結。 |
| `Cascadeur did not finish within …` | GUI 執行緒的工作逾時了，GUI 可能正在忙。如果是 `run_python`，請調高 `timeout`（最多 600）。 |
| `Lost connection to Cascadeur MCP Plus` | Cascadeur 可能已經關閉或當機。 |
| `Refused to click …` | 這是 Quick Rigging 防護。請先呼叫 `enter_rig_mode`。請看[安全](safety.zh-TW.md)。 |
| `Cascadeur's mocap model is not installed` | 請看[行為與限制](limitations.zh-TW.md#重定向綁定和動捕)。 |
