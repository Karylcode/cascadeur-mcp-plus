# Cascadeur MCP Plus

[![CI](https://github.com/Karylcode/cascadeur-mcp-plus/actions/workflows/ci.yml/badge.svg)](https://github.com/Karylcode/cascadeur-mcp-plus/actions/workflows/ci.yml)

[English](README.md)

Cascadeur MCP Plus 是一個 MCP 伺服器，執行在 [Cascadeur](https://cascadeur.com/) 裡面，讓 AI agent
（Claude Code、Codex CLI、Cursor、VS Code 等）可以用 Cascadeur 自己的功能做動畫：用數值設定姿勢，
讓 AutoPosing 帶動身體；打關鍵幀並設定時間軸的內插；執行 AutoPhysics 和清理；再用 Cascadeur 自己的
算繪器出片。agent 不必用滑鼠拖骨頭，也不用把截圖拼起來。

> **非官方專案。** 與 Nekki 沒有任何關聯，也未經 Nekki 背書。Cascadeur 是 Nekki Limited 的商標。
> 本專案不包含任何 Cascadeur 的程式碼，只透過 Cascadeur 的 Python API 和 Qt 與它互動。

## 概覽

- 伺服器提供 105 個工具，依主題分組列在「工具」一節。代理程式（stdio proxy）另外提供兩個工具，用來
  處理多個 Cascadeur 視窗（`list_instances` 和 `set_active_instance`）。
- 以 Cascadeur 2026.2 為目標，在 Windows 上用 2026.2.3 測試過。`get_status` 會列出這個版本的 Cascadeur
  無法使用的工具，呼叫它們會直接回傳明確的錯誤，不會做到一半才失敗。
- 典型流程：開啟場景、設定範圍、用 `set_pose` 擺姿勢、打關鍵幀並設定內插、用 `render_contact_sheet`
  檢查動作，最後用 `render_video` 輸出 MP4。可以直接執行的完整範例在 `examples/wave.md`。

## 需求

- Windows 10 或 11。macOS 和 Linux 沒有測試過，請見「平台支援」。
- Cascadeur 2026.2，需要內建的 MCP script server（在 2026.2.3 測試過）。
- Python 3.11 以上，給代理程式和 CLI 使用。它們只用標準函式庫，不需要 `pip install`。
- [uv](https://docs.astral.sh/uv/)（提供 `uvx`），用於一行安裝。手動安裝只需要 Python。
- 一個 MCP 用戶端：Claude Code、Codex CLI、Cursor、VS Code，或任何讀取 JSON `mcpServers` 設定的用戶端。

## 安裝

### 一行安裝

在這個專案的 checkout 資料夾裡執行：

```bat
uvx --from C:\path\to\cascadeur-mcp-plus cascadeur-mcp-plus setup
```

`setup` 會把伺服器（名稱為 `cascadeur-plus`）註冊到偵測到的每個 MCP 用戶端（Claude Code、Codex CLI、
Cursor 和 VS Code）。修改任何設定檔之前都會先備份。選項：

- `--dry-run` 只印出確切的變更，不寫入任何檔案。
- `--client claude-code`（或 `codex`、`cursor`、`vscode`）只改一個用戶端。
- `--command PATH` 讓用戶端改執行另一個執行檔。

`cascadeur-mcp-plus uninstall` 會移除註冊。`cascadeur-mcp-plus doctor` 會檢查 Cascadeur 的安裝、8765 和
48766 兩個連接埠、script server、MCP Plus 版本和偵測到的用戶端。

這個套件還沒有發佈到 PyPI，所以上面的指令要從 checkout 執行。發佈之後，安裝指令會變成
`uvx cascadeur-mcp-plus setup`。

### 手動安裝

Claude Code：

```bat
claude mcp add --scope user cascadeur-plus -- python -I C:\path\to\cascadeur-mcp-plus\proxy.py
```

其他用戶端（JSON 設定）：

```json
{
  "mcpServers": {
    "cascadeur-plus": {
      "command": "python",
      "args": ["-I", "C:\\path\\to\\cascadeur-mcp-plus\\proxy.py"]
    }
  }
}
```

如果 `python` 不在 `PATH` 上，請改用 `python.exe` 的完整路徑。

## 啟動 Cascadeur

MCP Plus 執行在 Cascadeur 裡面。代理程式沒有偵測到它在執行時，會透過 Cascadeur 的 script server（連接埠
8765）啟動它。請選擇 script server 的啟動方式：

- **自動啟動**（一般使用建議）：執行一次 `cascadeur-mcp-plus autostart enable`，見「自動啟動」。之後
  Cascadeur 啟動時會自動開啟兩個伺服器。
- **手動**：每次開啟 Cascadeur 後，點選 **Scripts → MCP → Start script server**。第一次呼叫工具時，MCP Plus
  會自動啟動；之後每次呼叫都在 1 秒內。

## 自動啟動

`cascadeur-mcp-plus autostart` 會讓 Cascadeur 啟動時自動開啟 script server 和 MCP Plus。

- `autostart status` 會顯示處理程式是否已安裝、使用哪個 runtime 副本，以及兩個伺服器是否有回應。
  加上 `--json` 可供腳本使用。
- `autostart enable` 會在 Cascadeur 的 `resources\scripts\python\events\application_started\` 資料夾裡安裝
  處理程式，並把 MCP Plus 複製到 `%LOCALAPPDATA%\cascadeur-mcp-plus\runtime\<版本>\`。寫入安裝資料夾需要
  系統管理員權限，所以 Windows 會跳出一次 UAC 提示。`--dry-run` 會先印出變更內容；`--yes` 會略過確認
  （UAC 提示仍然會出現）。
- `autostart disable` 會移除處理程式，並還原它取代過的檔案。
- runtime 副本是一份快照。更新套件後、`status` 顯示 runtime 版本和已安裝版本不同時，或想要較新的程式碼時，
  請重新執行 `enable`。`--runtime PATH` 改用 checkout 裡的 MCP Plus，供開發用；如果那個 checkout 被移動，
  處理程式會失敗，並且只把錯誤寫入日誌。
- 預設的 `--method admin` 是目前唯一可用的方式。`user` 尚未支援。

> **安全性：**自動啟動會開啟兩個沒有驗證的本機連接埠：8765（Cascadeur 的 script server）和 48766
> （MCP Plus）。這台電腦上的任何程式都能透過它們在 Cascadeur 裡執行 Python。不使用 agent 時，請執行
> `cascadeur-mcp-plus autostart disable`。

## 同時開啟多個 Cascadeur

MCP Plus 最多可以同時在 10 個 Cascadeur 視窗中執行，使用連接埠 48766 到 48775。

- 預設的實例（連接埠 48766）照上面的方式透過 script server 啟動。其他視窗需要在各自的 Python Console 裡
  啟動 MCP Plus：執行 checkout 裡的 `start_mcp_plus.py`。實例沒有開啟時，錯誤訊息會給出確切的指令。
- `list_instances`（代理程式的工具，不在工具表格裡）會列出有回應的實例，包括連接埠、處理程式編號、場景和
  版本，並標出目前的實例。
- `set_active_instance`（同樣是代理程式的工具）選擇沒有指定 `instance` 的呼叫要送到哪一個實例。它接受連接埠、
  處理程式編號，或場景名稱的一部分。
- 每個工具在單次呼叫中都可以指定 `instance`：連接埠（`48770`）、`port:48770`、`pid:1234`、`default`，或場景
  名稱或路徑的一部分。名稱同時符合多個視窗時會回傳錯誤並列出候選項目。呼叫永遠不會被猜測性地送到某個視窗。
- 這個選擇屬於單一個 stdio 代理程式處理程序，所以兩個 MCP 用戶端不會共用它。
- 只有預設實例會隨自動啟動一起開始，因為 8765 這個連接埠由所有視窗共用。

## 平台支援

- **Windows 10 和 11：支援，並已測試**，使用 Cascadeur 2026.2.3。本 README 寫到的所有內容都在 Windows 上確認過。
- **macOS 和 Linux：沒有測試。**程式碼裡有這兩個平台的路徑（資料和安裝資料夾、用戶端設定檔位置、視窗擷取），
  但沒有人在真正的機器上執行過。維護者只支援並驗證 Windows。單元測試不會在任何平台上執行 Cascadeur。
- 只限 Windows：自動啟動（需要 UAC，並寫入安裝資料夾）、Win32 截圖路徑，以及動作捕捉對話框的自動操作。
  在 macOS 或 Linux 上，請從 Cascadeur 的 Python Console 執行 `start_mcp_plus.py` 來啟動 MCP Plus。那裡的視窗
  擷取可能被阻擋（例如在 Wayland 上）；這時 `screenshot` 會回報錯誤，並建議改用 `render_image`。

## 完整範例

只要給 agent 一句話：*在 Cascy 身上做一段 2 秒的揮手動畫，並輸出 mp4*。它會呼叫的工具和參數，寫在
[examples/wave.md](examples/wave.md)。[examples/wave.py](examples/wave.py) 會透過 HTTP 對執行中的 MCP Plus
執行同樣的步驟，只使用標準函式庫：

1. `open_scene` 用新分頁開啟 Cascy 範例，`get_timeline` 讀取 fps。
2. `set_animation_range` 設定 2 秒（fps 為 30 時是第 0 到 59 格），`set_key` 在第 0 格為休息姿勢打關鍵幀。
3. `set_pose` 在八個影格上同時寫入右手肘和右手：手舉到肩膀以上、往外側伸出，接著左右揮動，手肘跟著彎曲。
   每次寫入都用 `autoposing: false` 和 `key: true`，所以兩個控制點都會落在設定的位置。
4. 最後一格用 `set_pose` 把手臂恢復成休息姿勢，`set_interpolation` 把關鍵幀設成 clamped Bezier。
5. `get_pose` 檢查手高於肩膀的程度，以及揮動的幅度。
6. `render_contact_sheet` 從正面輸出 mesh 的 PNG 格狀圖，`render_video` 從正面輸出 MP4，以背景工作執行，
   腳本會用 `get_job` 輪詢結果。
7. `close_scene` 用 `discard_changes` 關閉測試分頁，所以範例檔案不會被存檔。

在 Cascadeur 開著、MCP Plus 執行中時執行：

```bat
python examples\wave.py
```

腳本會把 `wave.mp4` 和 `wave_contact.png` 寫到 `%TEMP%\cascadeur-mcp-plus-tests\T56\`。用 `--out` 可以指定
其他資料夾。

## 工具

伺服器提供這 105 個工具（`tools/list`）。表格裡是簡短的摘要；完整的說明和參數結構是 agent 實際看到的內容。
每個工具只在表格中出現一次。

### 場景與檔案

| 工具 | 功能 |
|---|---|
| `new_scene` | 建立空白的場景分頁，並切換到它。 |
| `open_scene` | 用新分頁開啟 `.casc` 檔（需要絕對路徑）；不會取代目前的分頁。 |
| `save_scene` | 儲存目前的場景；給 `path` 時另存成新的 `.casc` 檔。取代既有檔案需要 `overwrite=true`。 |
| `list_open_scenes` | 列出開啟中的場景分頁，包括索引、名稱、路徑、是否為目前分頁，以及是否有未存檔的變更（Cascadeur 不提供時為 null）。 |
| `switch_scene` | 依索引或完整名稱切換到某個場景分頁。建議用名稱，因為進入 Rig 模式時分頁順序會重排，索引會改變。 |
| `close_scene` | 關閉一個場景分頁。有未存檔變更的場景會維持開啟，除非 `discard_changes=true`。 |
| `import_model` | 把 FBX 或 GLB 模型匯入目前場景，並進入 Rig 模式。不支援 USD。 |
| `import_animation` | 把 FBX 或 BVH 檔裡的動畫套用到角色上；`take` 可以用名稱或索引指定 take。 |
| `list_fbx_takes` | 列出 FBX 檔裡的動畫 take 名稱。 |
| `export_scene` | 把目前場景匯出成 FBX 或 GLB 檔（需要絕對路徑）。 |
| `get_scene_info` | 摘要目前的場景：名稱、路徑、影格、物件數量、選取、Rig behaviour 和授權旗標。 |
| `get_object_info` | 依完整或部分名稱描述物件：類型、id、behaviour 和其屬性名稱。 |

### 角色與選取

| 工具 | 功能 |
|---|---|
| `list_characters` | 列出角色，包括根關節、網格名稱、控制點數量和關節數量。 |
| `list_controllers` | 列出角色的控制點，包括別名、類型、世界座標（cm）和 AutoPosing 鎖定狀態。 |
| `select_objects` | 依名稱或別名選取物件；可以取代、加入或從選取中移除，效果同在視埠中點選。 |
| `get_selection` | 列出目前的選取，包括名稱、別名、類型、Cascadeur 型別、角色和 id。 |
| `clear_selection` | 取消目前場景中的所有選取。 |
| `enter_rig_mode` | 用 Cascadeur 自己的入口進入 Rig 模式。Quick Rigging 需要先進入。 |
| `list_rig_templates` | 列出 Cascadeur 內建的 Quick Rigging 範本。 |
| `quick_rig` | 用 Quick Rigging 為純骨架的角色建立 Rig。場景已有 Rig 時會拒絕，除非 `replace=true`。 |
| `retarget_animation` | 把另一個開啟中的場景（用分頁名稱指定）或 `.casc` 檔的動畫，重定向到目前的 Rig，可用於不同骨架之間。 |

### 姿勢與關鍵幀

#### AutoPosing 與姿勢

| 工具 | 功能 |
|---|---|
| `get_pose` | 讀取某個影格的控制點位置（cm）和旋轉（度，含四元數），只讀，不會改動任何東西。 |
| `set_pose` | 用數值設定位置、位移或旋轉並打關鍵幀，其餘身體由 AutoPosing 帶動。 |
| `mirror_pose` | 在某個影格把整個角色的姿勢左右鏡像，使用 Cascadeur 自己的 Mirror。 |
| `copy_pose` | 把某個影格的姿勢複製到 MCP Plus 的姿勢暫存區。暫存區在記憶體中，重新啟動後會清除。 |
| `paste_pose` | 把先前複製的姿勢貼到某個影格，可選擇鏡像。 |
| `reset_pose` | 把控制點重設為預設的 T-pose 或綁定（bind）姿勢。 |
| `get_autoposing_state` | 回報 AutoPosing 是否開啟。 |
| `autoposing_enable` | 開啟或關閉 AutoPosing。 |
| `autoposing_update` | 依目前的 AutoPosing 鎖定重新計算目前影格的姿勢，為一個復原步驟。 |
| `lock_controllers` | 依名稱或別名把控制點鎖定在某個影格，使用 Cascadeur 的 Autolock。Cascadeur 的 API 無法解除鎖定。 |

#### 時間軸與關鍵幀

| 工具 | 功能 |
|---|---|
| `get_timeline` | 回報目前影格、動畫的首尾影格、長度和 fps。只讀。 |
| `set_frame` | 把時間軸的播放頭移到指定影格。 |
| `set_animation_range` | 設定動畫的首尾影格。這個操作不能復原。 |
| `set_key` | 在指定影格為部分控制點打關鍵幀；省略 `controllers` 時為整個角色打關鍵幀。 |
| `delete_key` | 刪除指定影格上部分控制點（或整個角色）的關鍵幀。 |
| `list_keys` | 在一段影格範圍內，依控制點列出關鍵幀、每個區段的內插類型和區段。 |
| `select_frames` | 在時間軸上選取一段影格，之後的時間軸動作會套用到這段範圍。 |
| `create_cycle` | 用 Cascadeur 自己的 Create cycle 動作，把一段影格轉成時間軸循環。 |
| `remove_cycles` | 移除與某段範圍相交的時間軸循環；不給範圍時移除全部。 |

#### 內插與 IK/FK

| 工具 | 功能 |
|---|---|
| `set_interpolation` | 設定從 `start` 到 `end` 的區段內插：`bezier`、`bezier_clamped`、`bezier_viscous`、`linear`、`step` 或 `fixed`。 |
| `set_ik_fk` | 把從 `start` 到 `end` 的區段切換為 IK、FK 或 GR（全域 FK）。 |
| `reset_tangents` | 清除一段範圍內關鍵幀的自訂切線，恢復為連續切線。 |
| `reset_easing` | 把一段範圍內關鍵幀的 easing 權重重設為三分之一。 |

#### 動畫圖層

| 工具 | 功能 |
|---|---|
| `list_layers` | 列出動畫圖層樹，包括 id、路徑、顯示與鎖定狀態，以及物件數量。 |
| `create_layer` | 在資料夾下建立空的動畫圖層。 |
| `create_layer_folder` | 在圖層樹中建立資料夾。資料夾會附帶一個空圖層，因為 Cascadeur 2026.2.3 不接受空資料夾。 |
| `delete_layer` | 刪除圖層或資料夾。`force=true` 會先把物件移到預設圖層。 |
| `set_layer_visible` | 顯示或隱藏動畫圖層。 |
| `set_layer_locked` | 鎖定或解除鎖定動畫圖層。只有 MCP Plus 的寫入工具會遵守這個鎖定。 |
| `rename_layer` | 重新命名動畫圖層或資料夾。 |
| `move_to_layer` | 把控制點移到動畫圖層。 |

### 物理與 AI

| 工具 | 功能 |
|---|---|
| `get_physics_state` | 回報 AutoPhysics（Physics Assistant）的狀態：是否開啟、是否凍結、接觸點顯示、優先影格、影格和 fps。 |
| `physics_enable` | 開啟或關閉 Physics Assistant（AutoPhysics）。 |
| `physics_snap` | 把 AutoPhysics 的結果烘焙進動畫，套用到所有影格。背景工作。 |
| `physics_freeze` | 凍結或解除凍結 AutoPhysics 的結果。 |
| `physics_set_priority_frame` | 指定一個優先影格，求解器解其餘動畫時會保留該影格的姿勢。 |
| `physics_show_fulcrums` | 顯示或隱藏角色的接觸點（fulcrum）。 |
| `fix_foot_sliding` | 用 Cascadeur 的 Fulcrum Motion Cleaning 移除接觸區間中的腳滑。背景工作。 |
| `fix_collisions` | 用 Cascadeur 的 Collision Penetration Cleaning 把互相穿模的身體部位推開。背景工作。 |
| `prepare_keys_by_fulcrums` | 依接觸點的運動，用 Auto keys 為一段區間準備關鍵幀。清理前要先執行。背景工作。 |
| `ai_inbetween` | 用 Cascadeur 的 AI 補間填滿關鍵幀之間的影格。背景工作。 |
| `generate_motion` | 用 Cascadeur 的動作生成為一段區間產生新的動作。背景工作。 |
| `video_mocap` | 用 Cascadeur 內建的影片動作捕捉，把參考影片轉成角色動畫。背景工作。 |

### 看成果與出片

| 工具 | 功能 |
|---|---|
| `render_image` | 用 Cascadeur 自己的算繪器把 3D 畫面輸出成 PNG。預設資料夾在 `%TEMP%` 底下。 |
| `render_contact_sheet` | 把多個影格排成一張 PNG 格狀圖，每格標出影格編號，用來檢查動作。 |
| `render_video` | 透過 Cascadeur 的影片匯出視窗，把動畫輸出成 MP4。背景工作。 |
| `render_image_sequence` | 透過同一個匯出視窗，把動畫輸出成一個 PNG 影格資料夾。背景工作。 |

### 相機與顯示

| 工具 | 功能 |
|---|---|
| `get_camera` | 回傳視埠相機：位置、目標、類型、距離和垂直視角。 |
| `set_camera` | 依位置和/或目標移動視埠相機（cm）。 |
| `camera_preset` | 把相機切到角色的標準視角：`front`、`back`、`left`、`right`、`top` 或 `three_quarter`。 |
| `frame_objects` | 移動相機，讓指定的物件都在畫面內。 |
| `track_object` | 讓相機跟著某個物件或角色。不給名稱時，停止跟隨。 |
| `get_display_state` | 回報視埠的顯示模式、殘影模式、軌跡模式、視埠版面、剪影和表面狀態。 |
| `set_view_mode` | 切換視埠的顯示模式：`mesh`、`joint`、`box_controller`、`point_controller`、`autoposing`、`rigging_view` 或 `view`。 |
| `set_ghosts` | 設定殘影模式：`none`、`previous`、`next`、`neighbors`、`keyframes` 或 `selected_frames`。 |
| `set_trajectories` | 設定軌跡模式：`off`、`all`、`edges`、`edges_and_fixed` 或 `parents`。`only_key_frames` 可以限制只顯示關鍵幀。 |
| `set_viewport_layout` | 把視埠版面切換為 1、2 或 4 個視埠。 |
| `set_silhouette` | 開啟或關閉剪影顯示。 |
| `set_surface` | 開啟或關閉表面顯示。 |

### 介面操作（最後手段）

這些工具會像真人一樣操作介面。請先使用上面的專用工具。

| 工具 | 功能 |
|---|---|
| `ui_tree` | 最後手段：列出畫面上的介面元素，並附上 ref 供其他介面工具使用。 |
| `ui_click` | 最後手段：依 ref 或文字點擊元素，或點擊視窗座標。 |
| `ui_drag` | 最後手段：用滑鼠分段拖曳，拖曳期間按住按鍵。 |
| `ui_hover` | 最後手段：把滑鼠移到元素上，顯示提示或懸停選單。 |
| `ui_key` | 最後手段：按下按鍵或快速鍵，例如 `ctrl+s`。 |
| `ui_type` | 最後手段：在目前有焦點的元素中輸入文字。 |
| `ui_set_property` | 最後手段：設定元素的 QML 屬性。 |
| `ui_invoke` | 最後手段：呼叫元素上不需要參數的方法或訊號。 |
| `dialogs` | 列出目前顯示的對話框和彈出視窗，包括文字和按鈕。 |
| `screenshot` | 最後手段：擷取 Cascadeur 視窗或單一元素的畫面。 |
| `project_point` | 最後手段：把場景物件投影為視窗座標，供 `ui_click` 和 `ui_drag` 使用。 |

### 其他

| 工具 | 功能 |
|---|---|
| `get_status` | 回報 MCP Plus 和 Cascadeur 的版本、測試過的版本，以及這個版本無法使用的工具。 |
| `api_search` | 在 Cascadeur 的 Python 型別定義和官方範例中搜尋類別、函式和說明文字。 |
| `api_describe` | 顯示某個 `csc` 類別、函式或方法的型別定義和說明。 |
| `run_python` | 在 Cascadeur 的 GUI 執行緒中執行 Python，可直接使用 `csc`、`app`、`scene`、`view_scene` 和 `ui`。 |
| `get_log` | 讀取 Cascadeur 日誌的最後幾行，錯誤和 Python traceback 都在這裡。 |
| `batch` | 一次請求執行多個工具呼叫。 |
| `get_job` | 讀取背景工作的狀態、進度、結果或錯誤。 |
| `list_jobs` | 列出最近的背景工作，最新的排在前面。 |
| `cancel_job` | 取消執行中的背景工作。 |
| `undo` | 用 Cascadeur 自己的復原功能，復原最近一次或多次變更。 |
| `redo` | 重做最近一次或多次被復原的變更。 |
| `get_undo_history` | 列出最近的 `MCP:` 復原步驟，並標示已套用或已復原。 |
| `list_actions` | 列出目前介面中找到的 Cascadeur 動作 key。 |
| `call_action` | 依 key 觸發 Cascadeur 動作，效果同工具列按鈕或選單項目。有對應的動作時建議優先使用。 |

## 使用工具

- **單位與影格。**位置以公分為單位，旋轉以度為單位（Euler XYZ）。影格是從 0 開始的整數。
- **名稱。**可以傳入 Cascadeur 的物件名稱，或別名，例如 `left_hand`、`right_foot`、`pelvis` 或 `head`。
  `list_controllers` 會列出可用的名稱。名稱不明確時回傳 `candidates`；找不到時回傳 `closest` 建議。
- **結果。**成功是 `{"ok": true, ...}`；失敗是 `{"ok": false, "error": "...", "hint": "..."}`。請照著 hint 處理。
- **復原。**每個會改動場景的工具都會建立一個名為 `MCP: <工具名稱>` 的復原步驟。可以用 `undo` 或 Ctrl+Z 復原。
  `get_undo_history` 會列出這些步驟。部分步驟比較特別，見「已知限制」。
- **背景工作。**`render_video`、`render_image_sequence`、`physics_snap`、`fix_foot_sliding`、
  `fix_collisions`、`prepare_keys_by_fulcrums`、`ai_inbetween`、`generate_motion` 和 `video_mocap` 會立即回傳
  `job_id`。用 `get_job` 輪詢（`wait_s` 最多等 60 秒），用 `cancel_job` 停止工作。要檢查 `status`，因為失敗或
  取消的工作 `ok` 也是 true。伺服器重新啟動時仍在執行的工作會回報 `lost`。
- **用數值擺姿勢。**請使用 `set_pose`、`set_key` 和算繪工具。介面工具是最後手段。
- **版本差異。**需要這個 Cascadeur 版本缺少的 API 或動作的工具，會列在 `get_status` 中；呼叫它會回傳
  `ok: false`，而且不會執行任何東西。

## 輸出檔案

| 輸出 | 預設位置 |
|---|---|
| `render_image` | `%TEMP%\cascadeur-mcp-plus\renders\` |
| `render_video` | `%TEMP%\cascadeur-mcp-plus\videos\` |
| `render_image_sequence` | `%TEMP%\cascadeur-mcp-plus\sequences\` |
| `render_contact_sheet` | `%TEMP%\cascadeur-mcp-plus\contact_sheets\` |
| `video_mocap` 的參考影格 | `%TEMP%\cascadeur-mcp-plus\mocap-frames\<時間戳記>\`，執行後會刪除，除非 `keep_reference=true` |
| `examples/wave.py` | `%TEMP%\cascadeur-mcp-plus-tests\T56\` |

只有傳入 `overwrite=true` 才會取代既有檔案。算繪或匯出失敗時，原本的檔案會保留。

## 運作方式

```text
MCP 用戶端 ──stdio──► proxy.py ──HTTP──► Cascadeur 裡的 MCP Plus（127.0.0.1:48766）
                         │                       │ 呼叫都在 Cascadeur 的 GUI 執行緒上執行
                         └─ 沒在執行時 ──► Cascadeur script server（127.0.0.1:8765）/run → start_mcp_plus.py
```

- 代理程式就是 MCP 用戶端啟動的那個程式。它把呼叫轉送給 MCP Plus，需要時透過 script server 啟動 MCP Plus，
  Cascadeur 關閉時則提供最近一次的工具清單。工具有數值型的 `timeout` 參數時，會等待該時間（上限 600 秒）加上
  寬限時間；其他工具等 210 秒。
- MCP Plus 使用 Cascadeur 內附的 PySide6，與 Cascadeur 共用同一個 Qt 執行環境。它可以從 Python 存取正在執行的
  QML 介面、視窗和 Qt 的輸入系統。
- 點擊、拖曳和按鍵透過 Qt 的輸入路徑（QtTest）送出，所以走的是真人操作的同一條路。拖曳的每一步都是獨立的
  GUI 執行緒工作，AutoPosing 和其他即時更新會在每次移動之間執行。
- 算繪使用 Cascadeur 自己的算繪器（`RenderToFile`）。影片使用 Cascadeur 的影片匯出視窗。
- 截圖使用 Win32 的 `PrintWindow` API。從 Python 強制 Qt Quick 額外繪製（`QQuickWindow.grabWindow`）在測試時
  曾讓 Cascadeur 當機，所以 MCP Plus 不這樣做。
- 代理程式在送出呼叫之前，會先透過 `/health` 確認伺服器的身分，而且絕不會把呼叫轉送到被其他程式佔用的連接埠。

## 安全

- 兩個伺服器都只監聽 `127.0.0.1`，但它們會執行收到的任何 Python，就像 Cascadeur 官方的 script server 一樣。
  用完請關閉 script server；不使用 agent 時，請關閉自動啟動。
- 請用 `close_scene` 關閉場景。不要在 `run_python` 裡關閉場景，不要呼叫 `SceneManager.remove_application_scene`，
  也不要在同一次呼叫中操作剛被關閉的場景。這些操作在測試中都讓 Cascadeur 當機。
- 圖層鎖定只有 MCP Plus 的寫入工具會遵守。Cascadeur 的 Python API 不會檢查它，所以 `run_python` 仍然可以改動
  鎖定圖層上的物件。
- `ui_click` 和 `ui_invoke` 會拒絕點擊 Quick Rigging 的 **Add rig elements／Generate rig**，除非場景處於 Rig 模式
  （AnimationInfo 剛好 1 個）。在那個狀態下，Cascadeur 會丟出 `Rig mode several animationInfo` 然後當機。請先呼叫
  `enter_rig_mode`。

## 已知限制

- **只在一種環境下測試過。**Windows 上的 Cascadeur 2026.2.3。其他 Cascadeur 版本的行為可能不同。`get_status`
  會列出這個版本停用的工具，以及更新後需要重新確認的行為。
- **不是每個工具都能完整復原。**每個會改動場景的工具都是一個 `MCP:` 步驟，但帶有 AutoPosing 的 `set_pose` 需要
  復原兩次：自己的步驟，加上 Cascadeur 的 AutoPosing.Autolock。`set_animation_range` 不能復原。IK/FK、重定向、
  清理、循環、動作捕捉和 Quick Rigging 會執行 Cascadeur 自己的指令，所以一次 Ctrl+Z 可能無法完整還原，而且
  `get_undo_history` 不會列出它們。Cascadeur 重新啟動或載入場景後，復原紀錄會重新開始。
- **主要控制點沒有旋轉。**在 2026.2.3 裡它們只有位置。要旋轉時請改用盒子或關節，例如 `hand_Box_l`。
- **AutoPosing 可能把之後的姿勢重設。**`autoposing=true` 時，若 `set_pose` 寫在另一個已擺姿勢的影格之後，測試中
  AutoPosing 會把它重設：手會退回離目標約 70 cm 的位置。改用 `autoposing=false` 並寫入需要的控制點，就能準確落在
  設定的位置；`examples/wave.md` 有示範。沒有先前 AutoPosing 資料的影格，身體也幾乎不動，所以請先為影格打關鍵幀，
  再用 `get_pose` 檢查數值。
- **同一個影格的第二次 `set_pose` 可能復原第一次的一部分。**請把同一個影格的控制點一次寫完，或使用
  `autoposing=false`。
- **鎖定。**`lock_controllers` 不能解除鎖定，因為 2026.2.3 的 API 沒有解鎖功能。`list_open_scenes` 的 `modified`
  會是 null，因為 2026.2.3 沒有公開的未存檔旗標。
- **長時間的操作會讓介面停止回應。**所有呼叫都在 Cascadeur 的 GUI 執行緒上執行，所以長時間的工作會阻塞它。
  `get_scene_info` 這類需要 GUI 的呼叫，會排在正在執行的清理工作後面。請用短的間隔輪詢 `get_job`。
- **影片輸出。**`render_video` 和 `render_image_sequence` 會操作 Cascadeur 官方的影片匯出視窗。Cascadeur 不能
  最小化，也不能有其他匯出視窗開著。算繪器會在畫面角落畫上自己的 `Frame: N` 文字，mesh 的線框邊線也還會出現。
  匯出期間切換場景分頁時，曾看到進度停在 86%。Contact sheet 的相機是依控制點取景，所以控制點之外的頭髮或布料
  可能被切掉。
- **AI 與生成的動作。**`generate_motion` 會覆寫已擺好的關鍵幀，而且不會建立復原步驟，所以請先複製一份動畫。
  每個 Cascadeur 處理程序中第一次執行時，會載入模型約 20 到 26 秒，這段時間內的呼叫可能逾時。`ai_inbetween`
  需要 Pro 授權，並且至少要有兩個關鍵幀。
- **物理。**`physics_snap` 會套用到所有影格，而且做完 snap 後 Physics Assistant 會關閉，所以下一次 snap 前要
  再呼叫 `physics_enable`。`physics_set_priority_frame` 的影格必須在已打關鍵幀的範圍內。清理使用 Cascadeur 的
  預設設定，而且必須先執行 `prepare_keys_by_fulcrums`。判斷清理結果時，請看 `same_point_frames` 的比較，
  不要只看總量。
- **重定向與 Rig。**`retarget_animation` 需要 Pro 或 Studio 授權，只支援人形 Rig 之間的重定向，而且來源必須有
  關鍵幀。`quick_rig` 只適用於純骨架的角色。請先用 `import_model` 進入 Rig 模式：有一次實機執行時，`quick_rig`
  自己進入 Rig 模式就讓 Cascadeur 當機。
- **動作捕捉。**`video_mocap` 沒有公開的 Python API。它會操作 Cascadeur 的 Mocap 對話框，並需要模型檔
  `mocap.pt`（約 825 MB），第一次使用時會下載。它的動作品質和非 Pro 授權都沒有驗證，而且只能在 Windows 上使用。
- **匯入與匯出。**不支援 USD。`import_model` 只接受 FBX 和 GLB。`import_animation` 需要骨架的關節名稱相符，
  不同骨架之間請使用 `retarget_animation`。匯入失敗時，模型可能已經留在場景裡；請不要再匯入一次。
- **顯示設定是全域的。**視埠的顯示模式是介面狀態，不是場景資料。關閉測試場景並不會還原它們。
- **介面工具。**這些是最後手段。座標和版面在不同 Cascadeur 版本之間會改變。
- **多個視窗。**預設的 Cascadeur 關閉後約 60 秒內，呼叫可能會掛住最多 210 秒，而不是立即失敗。
- **尚未發佈到 PyPI。**請依上面的說明從 checkout 安裝。

## 設定

| 設定 | 預設值 | 位置 |
|---|---|---|
| `CASCADEUR_PLUS_URL` | `http://127.0.0.1:48766` | 代理程式的環境變數。設定預設的實例。 |
| `CASCADEUR_OFFICIAL_URL` | `http://127.0.0.1:8765` | 代理程式的環境變數。 |
| `CASCADEUR_DIR` 或 `CASCADEUR_HOME` | 自動偵測 | Cascadeur 的安裝資料夾，供 `setup`、`autostart` 和 `doctor` 使用。 |
| `CASCADEUR_DATA` | 平台預設的 Cascadeur 資料資料夾 | 資料資料夾，動作捕捉模型也放在這裡。 |
| `CASCADEUR_LOG` | 資料資料夾中的 `logs\cascadeur_log.log` | `get_log` 讀取的日誌檔。 |
| `CLAUDE_CONFIG_DIR`、`CODEX_HOME` | 用戶端的預設值 | `setup` 尋找用戶端設定檔的位置。 |

要用最新的程式碼啟動或重新啟動 MCP Plus，請在 Cascadeur 的 Python Console 裡執行（或透過 script server）：

```python
p = r"C:\path\to\cascadeur-mcp-plus\start_mcp_plus.py"
exec(open(p, encoding="utf-8").read(), {"__file__": p})
```

## 疑難排解

- 執行 `cascadeur-mcp-plus doctor`。它會回報安裝、連接埠、伺服器和用戶端，而且只有遇到真正的問題時才會以 1 結束。
- **Start script server 失敗並出現 `WinError 10013`：**連接埠 8765 被其他程式佔用。用
  `netstat -ano | findstr :8765` 找出是哪個程式，然後把它移開。
- **算繪被拒絕，或產生空白的圖：**Cascadeur 被最小化了。請先還原視窗，例如用 `screenshot` 並加上
  `restore=true`，再重試。
- **出現「Unavailable in this Cascadeur version」：**呼叫 `get_status`，它會列出每個工具缺少的 API 或動作。
- **呼叫送到了預期之外的視窗：**呼叫 `list_instances` 查看所有執行中的實例，然後用 `instance` 指定。
- **更新後工具消失：**重新啟動 MCP 用戶端。如果指令路徑改變了，請重新執行 `setup`；套件更新後，請重新執行
  `autostart enable`。

## 開發

```bash
python -m unittest discover -s tests -v   # 單元測試；不需要 Cascadeur
ruff check . && ruff format --check .
python tools/check_readme_tools.py        # 比對 README 工具表格與執行中的伺服器和註冊表
```

`tools/check_readme_tools.py --offline` 只比對註冊表。in-app 套件（`cascadeur_mcp_plus/`）需要 Cascadeur 的
Python，所以 `tests/live/` 的實機測試要在 Cascadeur 裡執行。修改 in-app 程式碼後，請再執行一次
`start_mcp_plus.py` 來載入新程式碼；不需要重新啟動 Cascadeur。

## 授權

[MIT](LICENSE)
