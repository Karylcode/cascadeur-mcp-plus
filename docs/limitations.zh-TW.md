[English](limitations.md) | **繁體中文** · [文件索引](README.zh-TW.md)

# 行為與限制

這些限制和行為是在 Windows 上用 Cascadeur 2026.2.3 測試時觀察到的。其他版本的 Cascadeur 可能有不同的行為。`get_status` 會列出這個 Cascadeur 版本無法使用的工具。

## 已測試的環境

- 只測試過 Windows 10 和 11 搭配 Cascadeur 2026.2.3。
- 下列部分取決於 Cascadeur 的版本：影片匯出視窗（`render_video`、`render_image_sequence`）、AutoPosing 鎖定（`set_pose`、`lock_controllers`）、動畫圖層、影片動捕、循環、AutoPhysics、AI 補間（`ai_inbetween`、`generate_motion`）、重定向和介面工具。

## 姿勢和 AutoPosing

- **AutoPosing 不一定能在每個影格上維持姿勢。** 測試中，在 `autoposing=true` 的情況下，`set_pose` 寫在另一個已擺姿勢的影格之後的影格上的姿勢被重設，手仍離目標差 22 到 51 cm。此時同一次呼叫會改成不用 AutoPosing，重新套用要求的值，所以指定的控制器會準確到位。結果會有 `autoposing_applied: false`、一個 `warning` 和三個復原步驟。該影格上身體的其餘部分不會跟著動，所以請為這些影格設定關鍵影格，並用 `get_pose` 檢查數值。控制器如果仍然偏差超過 `tolerance_cm`（預設 1.0 cm；旋轉為 1.0 度），呼叫就會回傳 `ok: false` 和錯誤。
- **寫入一個控制器，可能會移動它相鄰的控制器。** 綁定會維持手臂的長度，所以移動手會帶動手肘。綁定也可能拒絕某個值：被綁定拉回去的頭會以 `ok: false` 回報。把手肘寫成綁定無法維持的值，會偏差約 4 cm，所以 `examples/wave.py` 只寫入手。
- **在同一個影格上第二次呼叫 `set_pose`，可能會把第一次呼叫寫入的其他控制器還原。** 後備做法只會檢查該次呼叫中指定的控制器。需要身體跟著動時，請在一次呼叫中寫入該影格的所有控制器，或使用 `autoposing=false`。
- **主要控制點沒有旋轉。** 在 2026.2.3 中，它們只有位置。請改為旋轉 box 和關節，例如 `hand_Box_l`。

## 復原

- 每個會改變場景的工具都會建立一個 `MCP:` 復原步驟。使用 AutoPosing 的 `set_pose` 需要復原兩次：它自己的步驟，再加上 Cascadeur 的 AutoPosing.Autolock。上述的後備做法需要復原三次。
- `set_animation_range` 無法復原。
- IK/FK、重定向、清理、循環、動捕和 Quick Rigging 執行的是 Cascadeur 自己的指令。按一次 Ctrl+Z 可能無法完全復原它們，而且 `get_undo_history` 不會列出它們。
- 復原歷程會在 Cascadeur 重新啟動或載入場景時重新開始。

## 鎖定和場景狀態

- `lock_controllers` 無法解鎖，因為 2026.2.3 的 API 沒有解鎖功能。
- MCP Plus 的寫入工具會遵守動畫圖層的鎖定；Cascadeur 的 Python API 則不會。請看[安全](safety.zh-TW.md)。
- `list_open_scenes` 會把 `modified` 回報為 `null`，因為 2026.2.3 沒有公開的未儲存變更旗標。
- 顯示設定（檢視模式、殘影、軌跡、視埠版面、剪影和表面）是全域的介面狀態，不是場景資料。關閉測試用的場景並不會還原它們。

## 輸出和顯示

- `render_video`、`render_image_sequence` 和 `render_contact_sheet` 使用乾淨的 View 顯示來渲染。傳入 `keep_overlays=true` 可以照視埠目前的樣子渲染。View 模式使用模型自己的材質：Cascy 範例會呈現有光澤的銀色，不是 AutoPosing 檢視那種霧面的灰白色。
- 渲染器仍然會在角落畫出它自己的 `Frame: N` 標籤。在 Cascadeur 2026.2.3 中沒有找到可以關閉它的設定。
- 格狀圖會依控制點調整攝影機取景，所以位於控制點之外的頭髮或布料可能會被切掉。
- `render_video` 和 `render_image_sequence` 操作的是 Cascadeur 官方的 Export video 視窗。Cascadeur 不可以最小化，也不可以開著其他匯出視窗。在一次執行中，匯出期間切換場景分頁，使匯出停在 86%。

## 長時間的操作

- 呼叫會在 Cascadeur 的 GUI 執行緒上執行，所以長時間的工作會阻塞它。需要在 GUI 執行緒上執行的呼叫（例如 `get_scene_info`）會排在執行中的清理工作後面。請以短間隔輪詢 `get_job`，不要一次長時間等待。

## AI、生成動作和物理

- `generate_motion` 會覆寫已擺姿勢的關鍵影格，而且不會建立復原步驟，所以請先複製動畫。每個 Cascadeur 行程的第一次執行會花約 20 到 26 秒載入模型，這段時間內送出的呼叫可能會逾時。
- `ai_inbetween` 需要 Pro 授權和至少兩個關鍵影格。
- `physics_snap` 會套用到所有影格，而且 Physics Assistant 在 snap 之後會關閉，所以下一次 snap 之前要再呼叫一次 `physics_enable`。
- `physics_set_priority_frame` 需要一個落在關鍵影格範圍內的影格。
- 清理使用 Cascadeur 的預設設定，而且必須先執行 `prepare_keys_by_fulcrums`。請依 `same_point_frames` 的比較結果判斷清理的成效，不要看總數。

## 重定向、綁定和動捕

- `retarget_animation` 需要 Pro 或 Studio 授權，適用於人形綁定之間，而且來源必須有關鍵影格。
- `quick_rig` 用於只有骨架的角色。預設下它會自己進入 Rig 模式；在一次實機執行中，這一步讓 Cascadeur 當機了。請用 `import_model` 匯入模型（它會進入 Rig 模式），再呼叫 `quick_rig`；這個順序通過了實機測試。
- **`video_mocap` 是實驗性功能。** 在 Cascadeur 2026.2.3 的測試中，取消正在執行的動捕工作讓 Cascadeur 當機了兩次，原因還沒查明。呼叫前請先存檔，Cascadeur 的 Mocap 視窗開著時不要取消這個工作。
- `video_mocap` 沒有公開的 Python API。它操作 Cascadeur 的 Mocap 對話框，並且需要模型檔 `resources\mocap.pt`（約 825 MB），放在 Cascadeur 的資料夾（`CASCADEUR_DATA`）裡。檔案不存在時，呼叫會被拒絕並附上提示：用一支影片開啟一次 **View > Mocap**，讓 Cascadeur 下載模型，或把 `mocap.pt` 複製到正確的位置。
- `video_mocap` 的動作品質尚未用真實影片驗證，也沒有測試過 Pro 以外的授權。

## 匯入和匯出

- 不支援 USD。`import_model` 接受 FBX 和 GLB。
- `import_animation` 需要關節名稱相符。不同骨架之間請使用 `retarget_animation`。
- 匯入失敗時，模型可能還留在場景裡。請不要再匯入一次。

## 介面工具

- 介面工具是最後手段。座標和版面配置會隨 Cascadeur 的版本而改變，所以請優先使用 `actionId` 或畫面上可見的文字，不要用座標。
- 最小化的視窗什麼都畫不出來，所以請先用 `screenshot` 加上 `restore=true` 還原它。

## 綁定命名

- 別名已在 Cascadeur 的標準綁定，以及 Cascy、UE4 Mannequin 和 Roblox R15 範例上確認過。Mixamo 風格的名稱只做過單元測試。

## 尚未發布到 PyPI

- 請依[安裝](installation.zh-TW.md)的說明，從 checkout 安裝。
