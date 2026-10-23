"""Qt Quick UI inspection and input for Cascadeur.

Everything here must run on the GUI thread; the server's dispatcher guarantees that.
Cascadeur's bundled PySide6 shares the application's Qt runtime, so the live
QGuiApplication, its QQuickWindows and their QML item trees are reachable directly.
"""

from __future__ import annotations

import base64
import ctypes
import re
from ctypes import wintypes

import shiboken6
from PySide6.QtCore import QBuffer, QEvent, QIODevice, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QGuiApplication, QImage, QKeyEvent
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest

import csc

_QML_SUFFIX = re.compile(r"(_QMLTYPE_\d+|_QML_\d+)$")
_TAG = re.compile(r"<[^>]+>")
_TEXT_PROPS = ("text", "title", "toolTip", "placeholderText")
_DIALOG_TYPE = re.compile(r"Dialog|Popup|MessageBox", re.IGNORECASE)

# ref -> (window, item) from the most recent ui_tree / dialogs call.
_refs: dict[str, tuple[QQuickWindow, QQuickItem]] = {}


class UiError(Exception):
    pass


# ---------------------------------------------------------------------------
# Windows and items
# ---------------------------------------------------------------------------


def quick_windows(include_hidden: bool = False) -> list[QQuickWindow]:
    windows = []
    for window in QGuiApplication.topLevelWindows():
        if not window.inherits("QQuickWindow"):
            continue
        if not include_hidden and not window.isVisible():
            continue
        windows.append(shiboken6.wrapInstance(shiboken6.getCppPointer(window)[0], QQuickWindow))
    return windows


def is_main_window(window: QQuickWindow) -> bool:
    title = window.title()
    return title == "Cascadeur" or title.endswith(" - Cascadeur")


def _window_entry(index: int, window: QQuickWindow) -> dict:
    return {
        "window": index,
        "title": window.title(),
        "visible": window.isVisible(),
        "active": window.isActive(),
        "size": [window.width(), window.height()],
    }


def resolve_window(window: int | str | None = None, include_hidden: bool = False) -> tuple[int, QQuickWindow]:
    windows = quick_windows(include_hidden)
    if not windows:
        raise UiError("No Qt Quick windows found")
    if window is None or window == "":
        focus = QGuiApplication.focusWindow()
        for i, w in enumerate(windows):
            if focus is not None and shiboken6.getCppPointer(w)[0] == shiboken6.getCppPointer(focus)[0]:
                return i, w
        for i, w in enumerate(windows):
            if is_main_window(w):
                return i, w
        return 0, windows[0]
    if isinstance(window, int) or (isinstance(window, str) and window.isdigit()):
        index = int(window)
        if 0 <= index < len(windows):
            return index, windows[index]
        raise UiError(f"Window index {index} out of range (0..{len(windows) - 1})")
    for i, w in enumerate(windows):
        if str(window).lower() in w.title().lower():
            return i, w
    raise UiError(f"No window titled like {window!r}; open windows: {[w.title() for w in windows]}")


def _type_name(item: QQuickItem) -> str:
    return _QML_SUFFIX.sub("", item.metaObject().className())


def _clean_text(value: str, limit: int = 160) -> str:
    text = value
    if (
        "<" in text
        and (">" in text)
        and ("<html" in text.lower() or "<!doctype" in text.lower() or "<span" in text.lower())
    ):
        text = _TAG.sub(" ", text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _str_prop(item: QQuickItem, name: str) -> str | None:
    value = item.property(name)
    return value if isinstance(value, str) and value.strip() else None


def _is_clickable(item: QQuickItem) -> bool:
    mo = item.metaObject()
    return mo.indexOfSignal("clicked()") >= 0 or mo.indexOfSignal("pressed()") >= 0


def _is_input(type_name: str) -> bool:
    return any(k in type_name for k in ("TextInput", "TextField", "TextEdit", "SpinBox"))


def _scene_rect(item: QQuickItem) -> QRect:
    top_left = item.mapToScene(QPointF(0, 0))
    bottom_right = item.mapToScene(QPointF(item.width(), item.height()))
    return QRect(QPoint(int(top_left.x()), int(top_left.y())), QPoint(int(bottom_right.x()), int(bottom_right.y())))


def _on_screen(item: QQuickItem, window: QQuickWindow) -> bool:
    if not item.isVisible() or item.opacity() <= 0.0 or item.width() <= 0 or item.height() <= 0:
        return False
    rect = _scene_rect(item)
    return rect.right() > 0 and rect.bottom() > 0 and rect.left() < window.width() and rect.top() < window.height()


def _describe(item: QQuickItem) -> dict:
    type_name = _type_name(item)
    info: dict = {"type": type_name}
    for name in _TEXT_PROPS:
        value = _str_prop(item, name)
        if value:
            info[name] = _clean_text(value)
    action_id = _str_prop(item, "actionId")
    if action_id:
        info["actionId"] = action_id
    if item.objectName():
        info["objectName"] = item.objectName()
    # Cascadeur toolbar buttons report their on-state as `active`; reported only when true.
    for state in ("checked", "active"):
        if item.property(state) is True:
            info[state] = True
    if not item.isEnabled():
        info["enabled"] = False
    if _is_clickable(item):
        info["clickable"] = True
    if _is_input(type_name):
        info["input"] = True
    rect = _scene_rect(item)
    info["rect"] = [rect.x(), rect.y(), rect.width(), rect.height()]
    return info


def _is_interesting(info: dict) -> bool:
    return any(
        k in info
        for k in ("text", "title", "toolTip", "placeholderText", "actionId", "objectName", "clickable", "input")
    )


def _matches(info: dict, query: str | None) -> bool:
    if not query:
        return True
    q = query.lower()
    return any(
        q in str(info.get(k, "")).lower()
        for k in ("text", "title", "toolTip", "placeholderText", "actionId", "objectName", "type")
    )


def _walk(window: QQuickWindow, include_hidden: bool):
    """Yield every item of a window, including popups/overlays, pruning invisible subtrees."""
    root = window.contentItem()
    start = root.parentItem() or root
    stack = [start]
    while stack:
        item = stack.pop()
        if not include_hidden and not item.isVisible():
            continue
        yield item
        children = item.childItems()
        stack.extend(reversed(children))


def tree(
    window=None,
    query: str | None = None,
    include_hidden: bool = False,
    interactive_only: bool = False,
    limit: int = 150,
) -> dict:
    windows = quick_windows(include_hidden)
    selected = range(len(windows))
    if window not in (None, ""):
        selected = [resolve_window(window, include_hidden)[0]]
    _refs.clear()
    elements = []
    matched = 0
    for wi in selected:
        w = windows[wi]
        counter = 0
        for item in _walk(w, include_hidden):
            if not include_hidden and not _on_screen(item, w):
                continue
            info = _describe(item)
            if not _is_interesting(info):
                continue
            if interactive_only and not (info.get("clickable") or info.get("input") or info.get("actionId")):
                continue
            if not _matches(info, query):
                continue
            matched += 1
            if len(elements) >= limit:
                continue
            ref = f"{wi}:{counter}"
            counter += 1
            _refs[ref] = (w, item)
            elements.append({"ref": ref, **info})
    return {
        "windows": [_window_entry(i, w) for i, w in enumerate(windows)],
        "elements": elements,
        "matched": matched,
        "truncated": matched > len(elements),
        "hint": "Use an element's ref with ui_click / ui_type / ui_set_property / ui_invoke. Refs reset on every ui_tree or dialogs call.",
    }


def _find(
    query: str, window=None, index: int = 0, include_hidden: bool = False
) -> tuple[QQuickWindow, QQuickItem, list[dict]]:
    windows = quick_windows(include_hidden)
    selected = range(len(windows))
    if window not in (None, ""):
        selected = [resolve_window(window, include_hidden)[0]]
    q = query.lower()
    candidates = []
    for wi in selected:
        w = windows[wi]
        for item in _walk(w, include_hidden):
            if not include_hidden and not _on_screen(item, w):
                continue
            info = _describe(item)
            values = [
                str(info.get(k, "")) for k in ("text", "title", "toolTip", "actionId", "objectName", "placeholderText")
            ]
            exact = any(v.lower() == q for v in values)
            partial = exact or any(q in v.lower() for v in values)
            if not partial:
                continue
            score = (
                (2 if exact else 0)
                + (1 if info.get("clickable") or info.get("actionId") else 0)
                + (1 if info.get("enabled", True) else 0)
            )
            candidates.append((score, wi, w, item, info))
    if not candidates:
        raise UiError(f"No on-screen element matches {query!r}. Use ui_tree to inspect the UI.")
    candidates.sort(key=lambda c: -c[0])
    if index >= len(candidates):
        raise UiError(f"Only {len(candidates)} matches for {query!r}")
    _, _, w, item, _ = candidates[index]
    others = [{"window": c[1], **c[4]} for c in candidates[:8]]
    return w, item, others


def resolve_item(
    ref: str | None = None, query: str | None = None, window=None, index: int = 0
) -> tuple[QQuickWindow, QQuickItem, list[dict]]:
    if ref:
        if ref not in _refs:
            raise UiError(f"Unknown ref {ref!r}; call ui_tree again (refs reset on each call)")
        w, item = _refs[ref]
        if not shiboken6.isValid(item) or not shiboken6.isValid(w):
            raise UiError(f"Element {ref!r} no longer exists; call ui_tree again")
        return w, item, []
    if query:
        return _find(query, window, index)
    raise UiError("Provide ref or query")


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

_BUTTONS = {"left": Qt.LeftButton, "right": Qt.RightButton, "middle": Qt.MiddleButton}
_MODIFIERS = {
    "ctrl": Qt.ControlModifier,
    "control": Qt.ControlModifier,
    "shift": Qt.ShiftModifier,
    "alt": Qt.AltModifier,
    "meta": Qt.MetaModifier,
    "win": Qt.MetaModifier,
}
_KEY_ALIASES = {
    "enter": "Return",
    "return": "Return",
    "esc": "Escape",
    "escape": "Escape",
    "del": "Delete",
    "delete": "Delete",
    "backspace": "Backspace",
    "tab": "Tab",
    "space": "Space",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "insert": "Insert",
    "plus": "Plus",
    "minus": "Minus",
    "comma": "Comma",
    "period": "Period",
}


def _modifiers(names) -> Qt.KeyboardModifier:
    mods = Qt.NoModifier
    for name in names or []:
        key = str(name).strip().lower()
        if key not in _MODIFIERS:
            raise UiError(f"Unknown modifier {name!r}")
        mods |= _MODIFIERS[key]
    return mods


def _key(name: str) -> Qt.Key:
    raw = name.strip()
    alias = _KEY_ALIASES.get(raw.lower())
    if alias:
        return getattr(Qt.Key, f"Key_{alias}")
    if len(raw) == 1:
        attr = f"Key_{raw.upper()}"
        if hasattr(Qt.Key, attr):
            return getattr(Qt.Key, attr)
        return Qt.Key(ord(raw.upper()))
    if re.fullmatch(r"[Ff]\d{1,2}", raw):
        return getattr(Qt.Key, f"Key_F{raw[1:]}")
    attr = f"Key_{raw[0].upper()}{raw[1:]}"
    if hasattr(Qt.Key, attr):
        return getattr(Qt.Key, attr)
    raise UiError(f"Unknown key {name!r}")


_RIG_GENERATE = re.compile(r"add rig elements|generate rig", re.IGNORECASE)


def animation_info_count() -> int:
    scene = csc.app.get_application().current_scene().domain_scene()
    return len(scene.model_viewer().behaviour_viewer().get_behaviours("AnimationInfo"))


def _guard_rig_generation(labels: list, force: bool) -> None:
    """Refuse rig generation outside Rig mode, where Cascadeur crashes.

    The Quick Rigging button calls prototypes.qrt_prototypes.create.create_rig_elements_by_qrt,
    which needs exactly one AnimationInfo (created when entering Rig mode). Otherwise it raises
    "Rig mode several animationInfo" mid-way and the follow-up _flatten_qrt_twist_data crashes.
    """
    label = next((text for text in labels if text and _RIG_GENERATE.search(text)), None)
    if force or label is None:
        return
    count = animation_info_count()
    if count != 1:
        raise UiError(
            f"Refused to click {label!r}: the scene has {count} AnimationInfo but rig generation needs exactly 1, "
            "which exists only in Rig mode. Clicking now crashes Cascadeur. Call enter_rig_mode first "
            "(or pass force=true if you are sure)."
        )


def _labels_at(w: QQuickWindow, point: QPoint) -> list:
    labels = []
    for item in _walk(w, False):
        text = _str_prop(item, "text") or _str_prop(item, "toolTip")
        if text and _RIG_GENERATE.search(text) and _scene_rect(item).contains(point):
            labels.append(text)
    return labels


def click(
    ref=None, query=None, window=None, x=None, y=None, button="left", double=False, modifiers=None, index=0, force=False
) -> dict:
    mods = _modifiers(modifiers)
    btn = _BUTTONS.get(button)
    if btn is None:
        raise UiError(f"Unknown button {button!r}")
    others = []
    if x is not None and y is not None:
        _, w = resolve_window(window)
        point = QPoint(int(x), int(y))
        target = {"window": w.title(), "point": [point.x(), point.y()]}
        _guard_rig_generation(_labels_at(w, point), force)
    else:
        w, item, others = resolve_item(ref, query, window, index)
        center = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
        point = QPoint(int(center.x()), int(center.y()))
        target = {"window": w.title(), "point": [point.x(), point.y()], **_describe(item)}
        _guard_rig_generation(
            [target.get("text"), target.get("toolTip"), target.get("actionId")] + _labels_at(w, point), force
        )
        if not item.isEnabled():
            target["warning"] = "Element is disabled; the click may have no effect"
    if not w.isVisible():
        raise UiError(f"Window {w.title()!r} is hidden; show it before clicking")
    w.requestActivate()
    if double:
        QTest.mouseDClick(w, btn, mods, point)
    else:
        QTest.mouseClick(w, btn, mods, point)
    result = {"clicked": target}
    if len(others) > 1:
        result["other_matches"] = others[1:]
    return result


def hover(ref=None, query=None, window=None, x=None, y=None, index=0) -> dict:
    if x is not None and y is not None:
        _, w = resolve_window(window)
        point = QPoint(int(x), int(y))
    else:
        w, item, _ = resolve_item(ref, query, window, index)
        center = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
        point = QPoint(int(center.x()), int(center.y()))
    QTest.mouseMove(w, point)
    return {"hovered": {"window": w.title(), "point": [point.x(), point.y()]}}


def keys(sequence: str, window=None) -> dict:
    """Press key combos separated by spaces, e.g. 'ctrl+s' or 'ctrl+a Delete'."""
    _, w = resolve_window(window)
    pressed = []
    for combo in sequence.split():
        parts = [p for p in combo.split("+") if p]
        if not parts:
            continue
        key = _key(parts[-1])
        mods = _modifiers(parts[:-1])
        QTest.keyClick(w, key, mods)
        pressed.append(combo)
    return {"window": w.title(), "pressed": pressed}


def _input_within(item: QQuickItem) -> QQuickItem:
    """Return the item itself if it is a text input, else its first text-input descendant."""
    if _is_input(_type_name(item)):
        return item
    for child in _walk_item(item):
        if _is_input(_type_name(child)):
            return child
    return item


def type_text(text: str, ref=None, query=None, window=None, replace=False, submit=False, release_focus=False) -> dict:
    target = None
    if ref or query:
        w, item, _ = resolve_item(ref, query, window)
        item = _input_within(item)
        item.forceActiveFocus()
        target = _describe(item)
        if replace and item.property("text") is not None:
            item.setProperty("text", "")
    else:
        _, w = resolve_window(window)
    # QTest.keyClicks only targets QWidget, so send text-carrying key events to the
    # window; Qt Quick routes them to the active focus item. Works for any Unicode text.
    for ch in text:
        for event_type in (QEvent.KeyPress, QEvent.KeyRelease):
            QGuiApplication.sendEvent(w, QKeyEvent(event_type, 0, Qt.NoModifier, ch))
    if submit:
        QTest.keyClick(w, Qt.Key_Return)
    if release_focus:
        focused = w.activeFocusItem()
        if focused is not None:
            focused.setFocus(False)
    return {"window": w.title(), "typed": text, "target": target}


def set_property(ref: str, name: str, value) -> dict:
    w, item, _ = resolve_item(ref)
    before = item.property(name)
    ok = item.setProperty(name, value)
    return {"ok": bool(ok) or item.property(name) == value, "before": repr(before), "after": repr(item.property(name))}


def invoke(ref: str, method: str) -> dict:
    """Invoke a no-argument QML method or signal (e.g. 'clicked', 'toggle', 'open', 'close')."""
    from PySide6.QtCore import QMetaObject

    w, item, _ = resolve_item(ref)
    info = _describe(item)
    _guard_rig_generation([info.get("text"), info.get("toolTip"), info.get("actionId")], force=False)
    ok = QMetaObject.invokeMethod(item, method)
    return {"ok": bool(ok), "element": _describe(item)}


# ---------------------------------------------------------------------------
# Screenshots, dialogs, actions
# ---------------------------------------------------------------------------

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
_PW_CLIENTONLY_RENDERFULLCONTENT = 0x1 | 0x2
_WM_SYSCOMMAND = 0x0112
_SC_RESTORE = 0xF120


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def _is_minimized(w: QQuickWindow) -> bool:
    return bool(_user32.IsIconic(int(w.winId())))


def restore_window(window=None) -> dict:
    """Un-minimize a window at the OS level. Returns immediately; the caller waits outside the GUI thread."""
    _, w = resolve_window(window)
    hwnd = int(w.winId())
    was_minimized = bool(_user32.IsIconic(hwnd))
    if was_minimized:
        # Posted, not sent: the window procedure then runs from Cascadeur's normal event loop
        # instead of re-entering Qt from inside this dispatcher callback.
        _user32.PostMessageW(hwnd, _WM_SYSCOMMAND, _SC_RESTORE, 0)
    return {"window": w.title(), "restored": was_minimized}


def _capture_client(w: QQuickWindow) -> QImage:
    """Capture the window's client area through the OS compositor (PrintWindow).

    Deliberately avoids QQuickWindow.grabWindow(): forcing an extra Qt Quick render pass
    crashed Cascadeur, whose 3D viewport renders with its own OpenGL code.
    """
    hwnd = int(w.winId())
    rect = wintypes.RECT()
    _user32.GetClientRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise UiError(f"Window {w.title()!r} has no visible client area")
    window_dc = _user32.GetWindowDC(hwnd)
    memory_dc = _gdi32.CreateCompatibleDC(window_dc)
    bitmap = _gdi32.CreateCompatibleBitmap(window_dc, width, height)
    previous = _gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not _user32.PrintWindow(hwnd, memory_dc, _PW_CLIENTONLY_RENDERFULLCONTENT):
            raise UiError("PrintWindow failed")
        buffer = ctypes.create_string_buffer(width * height * 4)
        header = _BitmapInfoHeader(ctypes.sizeof(_BitmapInfoHeader), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        _gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(header), 0)
        return QImage(buffer.raw, width, height, width * 4, QImage.Format_RGB32).copy()
    finally:
        _gdi32.SelectObject(memory_dc, previous)
        _gdi32.DeleteObject(bitmap)
        _gdi32.DeleteDC(memory_dc)
        _user32.ReleaseDC(hwnd, window_dc)


def _mean_brightness(image) -> float:
    total = count = 0
    step_x = max(1, image.width() // 40)
    step_y = max(1, image.height() // 25)
    for y in range(0, image.height(), step_y):
        for x in range(0, image.width(), step_x):
            c = image.pixelColor(x, y)
            total += c.red() + c.green() + c.blue()
            count += 1
    return total / max(1, count) / 3


def screenshot(window=None, ref=None, max_width=1280, fmt="jpeg", quality=80) -> tuple[str, str, dict]:
    if ref:
        w, item, _ = resolve_item(ref)
    else:
        _, w = resolve_window(window)
        item = None
    if _is_minimized(w):
        raise UiError(
            f"Window {w.title()!r} is minimized and renders nothing. Call screenshot with restore=true to bring it back first."
        )
    image = _capture_client(w)
    meta = {"window": w.title(), "window_size": [w.width(), w.height()]}
    if _mean_brightness(image) < 1.0:
        meta["warning"] = (
            "Captured image is black; the window may be covered by a fullscreen app, minimized, or not rendering yet."
        )
    if item is not None:
        dpr = image.width() / max(1, w.width())
        rect = _scene_rect(item)
        image = image.copy(int(rect.x() * dpr), int(rect.y() * dpr), int(rect.width() * dpr), int(rect.height() * dpr))
        meta["crop_rect"] = [rect.x(), rect.y(), rect.width(), rect.height()]
    if max_width and image.width() > max_width:
        image = image.scaledToWidth(int(max_width), Qt.SmoothTransformation)
    source_width = meta.get("crop_rect", [0, 0, w.width()])[2]
    meta["image_size"] = [image.width(), image.height()]
    meta["window_units_per_pixel"] = round(source_width / max(1, image.width()), 4)
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    is_png = str(fmt).lower() == "png"
    image.save(buffer, "PNG" if is_png else "JPG", -1 if is_png else int(quality))
    data = base64.b64encode(bytes(buffer.data())).decode("ascii")
    return data, ("image/png" if is_png else "image/jpeg"), meta


def dialogs() -> dict:
    """List visible dialogs/popups and secondary windows, with their buttons as refs."""
    _refs.clear()
    found = []
    windows = quick_windows()
    for wi, w in enumerate(windows):
        if not is_main_window(w):
            found.append({"kind": "window", **_window_entry(wi, w)})
        counter = 0
        for item in _walk(w, False):
            type_name = _type_name(item)
            if not (_DIALOG_TYPE.search(type_name) or type_name == "QQuickPopupItem"):
                continue
            if not _on_screen(item, w):
                continue
            texts, buttons = [], []
            for child in _walk_item(item):
                if not _on_screen(child, w):
                    continue
                info = _describe(child)
                if info.get("clickable") and (info.get("text") or info.get("toolTip")):
                    ref = f"d{wi}:{counter}"
                    counter += 1
                    _refs[ref] = (w, child)
                    buttons.append({"ref": ref, "text": info.get("text") or info.get("toolTip")})
                elif info.get("text") and len(texts) < 12:
                    texts.append(info["text"])
            if texts or buttons:
                found.append({"kind": "dialog", "window": wi, "type": type_name, "texts": texts, "buttons": buttons})
    return {"dialogs": found, "hint": "Press a dialog button with ui_click(ref=...)."}


def _walk_item(root: QQuickItem):
    stack = [root]
    while stack:
        item = stack.pop()
        if not item.isVisible():
            continue
        yield item
        stack.extend(reversed(item.childItems()))


def list_actions(query: str | None = None, include_hidden: bool = True) -> dict:
    actions: dict[str, dict] = {}
    for w in quick_windows(include_hidden):
        for item in _walk(w, include_hidden):
            action_id = _str_prop(item, "actionId")
            if not action_id or (query and query.lower() not in action_id.lower()):
                continue
            entry = actions.setdefault(action_id, {"actionId": action_id})
            tip = _str_prop(item, "toolTip") or _str_prop(item, "text")
            if tip and "label" not in entry:
                entry["label"] = _clean_text(tip, 80)
            if item.isVisible():
                entry["on_screen"] = True
    return {
        "actions": sorted(actions.values(), key=lambda a: a["actionId"]),
        "note": "Collected from the live QML tree; actions of tools that have never been opened may be missing.",
    }


def call_action(key: str) -> dict:
    csc.app.get_application().get_action_manager().call_action(key)
    return {"called": key}


# ---------------------------------------------------------------------------
# Viewport projection and mouse drags
# ---------------------------------------------------------------------------

# Vertical field of view of Cascadeur's perspective viewport camera (standard symmetric frustum).
# The Python API does not expose it; 30 degrees was measured by overlaying projected control points
# on window captures (Cascadeur 2026.2.3, within ~1 px). Override with fov_deg if your view differs.
DEFAULT_VIEWPORT_FOV_DEG = 30.0


def _viewport_rect(w: QQuickWindow) -> QRect:
    for item in _walk(w, False):
        if _type_name(item) == "view::ViewportItem" and _on_screen(item, w):
            return _scene_rect(item)
    raise UiError("Main 3D viewport not found on screen")


def _object_world_position(scene, name: str):
    import numpy as np

    mv = scene.model_viewer()
    bv = mv.behaviour_viewer()
    dv = mv.data_viewer()
    ids = mv.get_objects(name)
    if not ids:
        raise UiError(f"No object named {name!r}")
    for behaviour in bv.get_behaviours(ids[0]):
        if bv.get_behaviour_name(behaviour) == "Transform":
            data = bv.get_behaviour_data(behaviour, "global_position")
            return np.array(dv.get_data_value(data, scene.get_current_frame()), dtype=float).flatten()
    raise UiError(f"Object {name!r} has no Transform.global_position")


def project_points(names, fov_deg: float = DEFAULT_VIEWPORT_FOV_DEG) -> dict:
    """Project scene objects to main-window coordinates of the main 3D viewport."""
    import math

    import numpy as np

    if isinstance(names, str):
        names = [names]
    app = csc.app.get_application()
    view_scene = app.current_scene()
    scene = view_scene.domain_scene()
    domain_viewport = next(
        (v.domain_viewport() for v in view_scene.viewports() if v.domain_viewport().is_main()),
        view_scene.viewports()[0].domain_viewport(),
    )
    camera = domain_viewport.camera_struct()
    if "PERSPECTIVE" not in str(camera.type):
        raise UiError(f"Only perspective cameras are supported (current: {camera.type})")
    eye = np.array(camera.position, dtype=float).flatten()
    target = np.array(camera.target, dtype=float).flatten()
    _, w = resolve_window()
    rect = _viewport_rect(w)
    forward = (target - eye) / np.linalg.norm(target - eye)
    side = np.cross(forward, [0.0, 1.0, 0.0])
    side /= np.linalg.norm(side)
    up = np.cross(side, forward)
    tan_half = math.tan(math.radians(fov_deg) / 2)
    aspect = rect.width() / rect.height()
    points = {}
    for name in names:
        world = _object_world_position(scene, name)
        d = world - eye
        depth = float(d @ forward)
        entry = {"world": [round(float(v), 3) for v in world], "depth": round(depth, 2)}
        if depth <= 0:
            entry["on_screen"] = False
            entry["error"] = "behind the camera"
        else:
            x = rect.x() + ((float(d @ side) / (tan_half * aspect)) / depth + 1) / 2 * rect.width()
            y = rect.y() + (1 - (float(d @ up) / tan_half) / depth) / 2 * rect.height()
            entry["window_xy"] = [round(x, 1), round(y, 1)]
            entry["on_screen"] = rect.contains(QPoint(int(x), int(y)))
        points[name] = entry
    return {
        "points": points,
        "viewport_rect": [rect.x(), rect.y(), rect.width(), rect.height()],
        "fov_deg": fov_deg,
        "camera": {"position": [round(float(v), 2) for v in eye], "target": [round(float(v), 2) for v in target]},
    }


# State of the drag in progress: each step runs as its own GUI-thread job so Cascadeur keeps
# processing events (rendering, AutoPosing updates) between mouse moves.
_drag: dict = {}


def drag_begin(
    ref=None,
    query=None,
    obj=None,
    window=None,
    x=None,
    y=None,
    to_x=None,
    to_y=None,
    dx=None,
    dy=None,
    button="left",
    modifiers=None,
    index=0,
    method="qtest",
    fov_deg=DEFAULT_VIEWPORT_FOV_DEG,
) -> dict:
    if _drag:
        drag_end()
    btn = _BUTTONS.get(button)
    if btn is None:
        raise UiError(f"Unknown button {button!r}")
    mods = _modifiers(modifiers)
    target = {}
    if obj:
        _, w = resolve_window(window)
        projected = project_points([obj], fov_deg)["points"][obj]
        if not projected.get("on_screen"):
            raise UiError(f"{obj!r} is not visible in the viewport: {projected}")
        start = QPointF(*projected["window_xy"])
        target = {"object": obj, "world": projected["world"]}
    elif x is not None and y is not None:
        _, w = resolve_window(window)
        start = QPointF(float(x), float(y))
    else:
        w, item, _ = resolve_item(ref, query, window, index)
        start = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
        target = _describe(item)
    if to_x is not None and to_y is not None:
        end = QPointF(float(to_x), float(to_y))
    elif dx is not None or dy is not None:
        end = QPointF(start.x() + float(dx or 0), start.y() + float(dy or 0))
    else:
        raise UiError("Provide to_x/to_y or dx/dy for the end point")
    if method not in ("qtest", "event"):
        raise UiError("method must be 'qtest' or 'event'")
    w.requestActivate()
    _drag.update(window=w, button=btn, modifiers=mods, method=method, pos=QPoint(round(start.x()), round(start.y())))
    _mouse(QEvent.MouseButtonPress, _drag["pos"])
    return {
        "window": w.title(),
        "start": [_drag["pos"].x(), _drag["pos"].y()],
        "end": [round(end.x()), round(end.y())],
        "target": target,
    }


def drag_move(x: float, y: float) -> dict:
    if not _drag:
        raise UiError("No drag in progress")
    _drag["pos"] = QPoint(round(x), round(y))
    _mouse(QEvent.MouseMove, _drag["pos"])
    return {"pos": [_drag["pos"].x(), _drag["pos"].y()]}


def drag_end() -> dict:
    if not _drag:
        return {"released": False}
    pos = _drag["pos"]
    try:
        _mouse(QEvent.MouseButtonRelease, pos)
    finally:
        _drag.clear()
    return {"released": True, "end": [pos.x(), pos.y()]}


def _mouse(kind, pos: QPoint) -> None:
    w, btn, mods = _drag["window"], _drag["button"], _drag["modifiers"]
    if _drag["method"] == "qtest":
        # QTest routes through QWindowSystemInterface, so Qt's global mouse state (pressed
        # buttons, grabber) is updated exactly as for a physical mouse.
        if kind == QEvent.MouseButtonPress:
            QTest.mousePress(w, btn, mods, pos)
        elif kind == QEvent.MouseButtonRelease:
            QTest.mouseRelease(w, btn, mods, pos)
        else:
            QTest.mouseMove(w, pos)
        return
    from PySide6.QtGui import QMouseEvent

    local = QPointF(pos)
    global_pos = QPointF(w.mapToGlobal(pos))
    if kind == QEvent.MouseButtonPress:
        event = QMouseEvent(kind, local, local, global_pos, btn, btn, mods)
    elif kind == QEvent.MouseButtonRelease:
        event = QMouseEvent(kind, local, local, global_pos, btn, Qt.NoButton, mods)
    else:
        event = QMouseEvent(kind, local, local, global_pos, Qt.NoButton, btn, mods)
    QGuiApplication.sendEvent(w, event)
