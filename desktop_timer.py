# -*- coding: utf-8 -*-
"""
Transparent Timer · 桌面透明计时器
==================================

无边框、始终置顶、背景完全透明的桌面计时器。
为「速度训练」设计：刷题、答题比赛、打字测速、番茄钟、演讲限时都能用。

    双击数字      开始 / 暂停
    滚轮          放大 / 缩小
    左键拖动      移动位置
    右键          菜单
    全局热键      开始/暂停、记一圈、显示/隐藏（都可自定义）

依赖：Python 3 标准库（tkinter），无需安装第三方包。
"""

import csv
import json
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

try:
    import winsound
except Exception:  # 非 Windows 环境
    winsound = None


# ---------------------------------------------------------------- 常量

# 透明键色：该颜色的像素完全透明，且不接收鼠标事件
KEY = "#0b0c0d"

# 主题：(主体色, 描边色)
THEMES = {
    "白字": ("#FFFFFF", "#000000"),
    "黑字": ("#111111", "#FFFFFF"),
    "金色": ("#FFC94D", "#2A1A00"),
    "青色": ("#5FE1FF", "#001A20"),
    "朱红": ("#FF5A5F", "#2A0000"),
    "薄荷": ("#6EE7B7", "#00251A"),
}

OVER_COLOR = "#FF4D4F"       # 超过目标用时
LAP_FLASH_COLOR = "#5BE49B"  # 记圈瞬间的反馈色

DEFAULTS = {
    "x": None,
    "y": None,
    "font_size": 70,
    "mode": "up",            # up = 正计时, down = 倒计时
    "down_total": 300,
    "opacity": 1.0,
    "topmost": True,
    "show_ms": True,
    "show_stats": True,
    "locked": False,
    "theme": "白字",
    "target": 0.0,           # 目标用时（秒），0 表示不设
    "laps": [],              # 记圈成绩（秒）
    "hotkey": {"mods": 0x0001, "vk": 0x77, "enabled": True},       # Alt+F8
    "hotkey_lap": {"mods": 0x0001, "vk": 0x78, "enabled": True},   # Alt+F9
    "hotkey_visible": False,                                        # Ctrl+Alt+H
}

MAX_LAPS = 500

# 倒计时预设：名称 -> 秒
PRESETS = [
    ("番茄钟 25:00", 25 * 60),
    ("短休息 5:00", 5 * 60),
    ("演讲 3:00", 3 * 60),
    ("辩论 4:00", 4 * 60),
    ("面试答题 5:00", 5 * 60),
    ("HIIT 间歇 0:45", 45),
]

# 目标用时预设（秒）
TARGET_PRESETS = [5, 8, 10, 15, 20, 30, 45, 60]


# ---------------------------------------------------------------- 工具


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(app_dir(), "timer_config.json")


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, dict):
            for k in DEFAULTS:
                if k in data:
                    cfg[k] = data[k]
    except Exception:
        pass

    if cfg.get("theme") not in THEMES:
        cfg["theme"] = DEFAULTS["theme"]
    if cfg.get("mode") not in ("up", "down"):
        cfg["mode"] = "up"
    try:
        cfg["font_size"] = max(20, min(420, int(cfg["font_size"])))
    except Exception:
        cfg["font_size"] = DEFAULTS["font_size"]
    try:
        cfg["target"] = max(0.0, float(cfg.get("target") or 0))
    except Exception:
        cfg["target"] = 0.0

    for key in ("hotkey", "hotkey_lap"):
        v = cfg.get(key)
        if not isinstance(v, dict) or "mods" not in v or "vk" not in v:
            cfg[key] = dict(DEFAULTS[key])

    laps = cfg.get("laps")
    if not isinstance(laps, list):
        laps = []
    clean = []
    for v in laps:
        try:
            f = float(v)
        except Exception:
            continue
        if f > 0:
            clean.append(round(f, 2))
    cfg["laps"] = clean[-MAX_LAPS:]
    return cfg


def save_config(cfg):
    try:
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(cfg, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        pass


def parse_duration(text):
    """'25' -> 25 分钟; '5:00' -> 5 分; '1:30:00' -> 1.5 小时"""
    text = (text or "").strip().replace("：", ":")
    if not text:
        return None
    parts = text.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if any(n < 0 for n in nums):
        return None
    if len(parts) == 1:
        return nums[0] * 60.0
    if len(parts) == 2:
        return nums[0] * 60.0 + nums[1]
    if len(parts) == 3:
        return nums[0] * 3600.0 + nums[1] * 60.0 + nums[2]
    return None


def fmt_duration(seconds):
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%d:%02d" % (m, s)


def fmt_lap(sec):
    """成绩显示：小于 60 秒用 8.42s，超过用 1:08.4"""
    if sec < 60:
        return "%.2fs" % sec
    m = int(sec // 60)
    s = sec - m * 60
    return "%d:%04.1f" % (m, s)


def dim_color(hex_color, factor=0.55):
    """按比例压暗一个 #RRGGBB 颜色"""
    try:
        h = hex_color.lstrip("#")
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
        r, g, b = (int(c * factor) for c in (r, g, b))
        return "#%02x%02x%02x" % (r, g, b)
    except Exception:
        return hex_color


def play_alert():
    if winsound is None:
        return

    def run():
        try:
            for _ in range(3):
                winsound.Beep(1046, 150)
                time.sleep(0.05)
                winsound.Beep(1318, 220)
                time.sleep(0.10)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


def stats_of(laps):
    """返回 (次数, 平均, 最快, 最慢, 中位数)"""
    if not laps:
        return 0, 0.0, 0.0, 0.0, 0.0
    n = len(laps)
    avg = sum(laps) / n
    ordered = sorted(laps)
    mid = n // 2
    median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
    return n, avg, min(laps), max(laps), median


# ---------------------------------------------------------------- 全局热键

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

WM_HOTKEY = 0x0312

HK_TOGGLE = 0xB001
HK_LAP = 0xB002
HK_VISIBLE = 0xB003
HK_VISIBLE_MODS = MOD_CONTROL | MOD_ALT
HK_VISIBLE_VK = 0x48  # H

_VK_SPECIAL = {
    "space": 0x20, "Return": 0x0D, "KP_Enter": 0x0D, "Escape": 0x1B,
    "Tab": 0x09, "BackSpace": 0x08, "Delete": 0x2E, "Insert": 0x2D,
    "Home": 0x24, "End": 0x23, "Prior": 0x21, "Next": 0x22,
    "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28,
    "Pause": 0x13, "Scroll_Lock": 0x91, "Print": 0x2C,
    "minus": 0xBD, "equal": 0xBB, "bracketleft": 0xDB, "bracketright": 0xDD,
    "backslash": 0xDC, "semicolon": 0xBA, "apostrophe": 0xDE,
    "comma": 0xBC, "period": 0xBE, "slash": 0xBF, "grave": 0xC0,
}

_VK_LABEL = {
    0x20: "Space", 0x0D: "Enter", 0x1B: "Esc", 0x09: "Tab", 0x08: "Backspace",
    0x2E: "Delete", 0x2D: "Insert", 0x24: "Home", 0x23: "End",
    0x21: "PageUp", 0x22: "PageDown",
    0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
    0x13: "Pause", 0x91: "ScrollLock", 0x2C: "PrintScreen",
    0xBD: "-", 0xBB: "=", 0xDB: "[", 0xDD: "]", 0xDC: "\\",
    0xBA: ";", 0xDE: "'", 0xBC: ",", 0xBE: ".", 0xBF: "/", 0xC0: "`",
}

MODIFIER_KEYSYMS = {
    "Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R",
    "Super_L", "Super_R", "Meta_L", "Meta_R", "Caps_Lock", "Num_Lock",
}


def keysym_to_vk(keysym, keycode=0):
    """把 tkinter 的按键转换成 Windows 虚拟键码"""
    if keysym in _VK_SPECIAL:
        return _VK_SPECIAL[keysym]
    if len(keysym) >= 2 and keysym[0] == "F" and keysym[1:].isdigit():
        n = int(keysym[1:])
        if 1 <= n <= 24:
            return 0x6F + n
    if len(keysym) == 1:
        ch = keysym.upper()
        if "A" <= ch <= "Z" or "0" <= ch <= "9":
            return ord(ch)
    if 1 <= keycode <= 254:
        return keycode
    return None


def vk_to_label(vk):
    if vk in _VK_LABEL:
        return _VK_LABEL[vk]
    if 0x70 <= vk <= 0x87:
        return "F%d" % (vk - 0x6F)
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk)
    return "VK_%02X" % vk


def hotkey_label(mods, vk):
    parts = []
    if mods & MOD_CONTROL:
        parts.append("Ctrl")
    if mods & MOD_ALT:
        parts.append("Alt")
    if mods & MOD_SHIFT:
        parts.append("Shift")
    if mods & MOD_WIN:
        parts.append("Win")
    parts.append(vk_to_label(vk))
    return "+".join(parts)


def hotkey_valid(mods, vk):
    """必须带修饰键，或者是 F1~F12 这类功能键"""
    if mods:
        return True
    return 0x70 <= vk <= 0x7B


class HotkeyListener(threading.Thread):
    """独立线程 + message-only 窗口，专门接收 WM_HOTKEY。

    RegisterHotKey 必须由拥有窗口的线程执行（跨线程调用会静默失败），
    因此主线程通过 cmd 队列下发指令、result 队列取回结果。
    """

    def __init__(self):
        super().__init__(daemon=True)
        self.queue = queue.Queue()
        self.cmd = queue.Queue()
        self.result = queue.Queue()
        self.ready = threading.Event()
        self.hwnd = None
        self.error = None
        self.user32 = None
        self._proc = None
        self._running = True

    def run(self):
        try:
            import ctypes
            from ctypes import wintypes

            u = ctypes.windll.user32
            k = ctypes.windll.kernel32
            self.user32 = u

            wndproc_type = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                wintypes.WPARAM, wintypes.LPARAM,
            )

            def _proc(hwnd, msg, wparam, lparam):
                if msg == WM_HOTKEY:
                    self.queue.put(int(wparam))
                    return 0
                return u.DefWindowProcW(hwnd, msg, wparam, lparam)

            self._proc = wndproc_type(_proc)

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ("style", ctypes.c_uint),
                    ("lpfnWndProc", ctypes.c_void_p),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", ctypes.c_void_p),
                    ("hIcon", ctypes.c_void_p),
                    ("hCursor", ctypes.c_void_p),
                    ("hbrBackground", ctypes.c_void_p),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR),
                ]

            u.DefWindowProcW.argtypes = [
                wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM
            ]
            u.DefWindowProcW.restype = ctypes.c_ssize_t
            u.CreateWindowExW.argtypes = [
                wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
            ]
            u.CreateWindowExW.restype = wintypes.HWND
            u.PeekMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
            ]
            u.RegisterHotKey.argtypes = [
                wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint
            ]
            u.RegisterHotKey.restype = wintypes.BOOL
            u.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]

            wc = WNDCLASSW()
            wc.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p)
            wc.hInstance = k.GetModuleHandleW(None)
            wc.lpszClassName = "TkTimerHotkeySink"
            u.RegisterClassW(ctypes.byref(wc))

            HWND_MESSAGE = wintypes.HWND(-3)
            self.hwnd = u.CreateWindowExW(
                0, "TkTimerHotkeySink", "sink", 0, 0, 0, 0, 0,
                HWND_MESSAGE, None, wc.hInstance, None,
            )
            if not self.hwnd:
                self.error = "CreateWindowEx failed"
        except Exception as exc:  # pragma: no cover
            self.error = repr(exc)
        finally:
            self.ready.set()

        if self.error or not self.hwnd:
            return

        msg = wintypes.MSG()
        PM_REMOVE = 0x0001
        while self._running:
            while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
            try:
                while True:
                    self._exec(self.cmd.get_nowait())
            except queue.Empty:
                pass
            time.sleep(0.012)

    def _exec(self, cmd):
        u = self.user32
        kind = cmd[0]
        if kind == "set":
            _, hid, mods, vk = cmd
            u.UnregisterHotKey(self.hwnd, hid)
            ok = bool(u.RegisterHotKey(self.hwnd, hid, mods, vk))
            self.result.put((hid, ok))
        elif kind == "clear":
            _, hid = cmd
            u.UnregisterHotKey(self.hwnd, hid)
            self.result.put((hid, True))

    def set_hotkey(self, hid, mods, vk, timeout=1.0):
        self.cmd.put(("set", hid, mods, vk))
        end = time.time() + timeout
        while time.time() < end:
            try:
                r = self.result.get(timeout=0.05)
            except queue.Empty:
                continue
            if r[0] == hid:
                return r[1]
        return False

    def clear_hotkey(self, hid):
        self.cmd.put(("clear", hid))
        end = time.time() + 0.5
        while time.time() < end:
            try:
                r = self.result.get(timeout=0.05)
            except queue.Empty:
                continue
            if r[0] == hid:
                return r[1]
        return False

    def stop(self):
        self._running = False


# ---------------------------------------------------------------- 开机自启

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "DesktopTransparentTimer"


def _startup_command():
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        cand = exe[:-10] + "pythonw.exe"
        if os.path.exists(cand):
            exe = cand
    script = os.path.abspath(__file__)
    return '"%s" "%s"' % (exe, script)


def autostart_enabled():
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
        return True
    except Exception:
        return False


def set_autostart(enable):
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS) as k:
            if enable:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, _startup_command())
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 主程序


class TimerApp:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()

        self.running = False
        self.acc = 0.0
        self.t0 = None
        self.finished = False

        self.W = 10
        self.H = 10
        self.x = 0
        self.y = 0
        self._drag = None
        self._moved = False
        self._last_sig = None
        self._submenus = []
        self._dialog = None
        self._hk_dialog = None
        self._stats_win = None
        self._hidden = False
        self._colon_on = True
        self._blink_ts = 0.0
        self._lap_flash_until = 0.0

        self.stroke_items = []
        self.main_items = {}
        self.sub_item = None

        self._build_window()
        self._build_fonts()
        self._build_canvas()
        self.menu = tk.Menu(self.root, tearoff=0, font=("Microsoft YaHei UI", 9))
        self._init_hotkey()

        self.rebuild(force=True)
        self._place_initial()

        if not os.path.exists(CONFIG_PATH):
            self.root.after(400, self.show_hint)

        self.root.after(25, self.tick)

    # ------------------------------------------------------------ 窗口

    def _build_window(self):
        r = self.root
        r.title("透明计时器")
        r.overrideredirect(True)
        r.configure(bg=KEY)
        r.attributes("-topmost", 1 if self.cfg["topmost"] else 0)
        try:
            r.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        if self.cfg["opacity"] < 1.0:
            try:
                r.attributes("-alpha", float(self.cfg["opacity"]))
            except tk.TclError:
                pass

    def _build_fonts(self):
        try:
            families = set(tkfont.families())
        except tk.TclError:
            families = set()
        self.family = "Courier"
        for name in ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New"):
            if name in families:
                self.family = name
                break

        size = int(self.cfg["font_size"])
        self.font_main = tkfont.Font(family=self.family, size=size, weight="bold")
        self.font_frac = tkfont.Font(
            family=self.family, size=max(12, int(size * 0.56)), weight="bold"
        )
        self.font_sub = tkfont.Font(
            family="Microsoft YaHei UI", size=max(10, min(19, int(size * 0.24)))
        )

    def _build_canvas(self):
        self.canvas = tk.Canvas(self.root, bg=KEY, highlightthickness=0, bd=0, takefocus=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_motion)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Double-Button-1>", self.on_double)
        self.canvas.bind("<Button-3>", self.on_menu)
        self.canvas.bind("<MouseWheel>", self.on_wheel)

    def _place_initial(self):
        sw = max(800, self.root.winfo_screenwidth())
        sh = max(600, self.root.winfo_screenheight())
        if self.cfg["x"] is None or self.cfg["y"] is None:
            self.x = max(0, sw - self.W - 80)
            self.y = 80
        else:
            self.x = max(-3840, min(int(self.cfg["x"]), sw + 3840 - 60))
            self.y = max(-2160, min(int(self.cfg["y"]), sh + 2160 - 60))
        self.root.geometry("%dx%d+%d+%d" % (self.W, self.H, self.x, self.y))

    # ------------------------------------------------------------ 时间

    def elapsed(self):
        e = self.acc
        if self.running and self.t0 is not None:
            e += time.monotonic() - self.t0
        return e

    def time_parts(self):
        """返回 (主文本, 小数文本)，小数文本可能为空"""
        show_ms = self.cfg["show_ms"]
        if self.cfg["mode"] == "down":
            remain = max(0.0, float(self.cfg["down_total"]) - self.elapsed())
            if show_ms:
                total = int(math.floor(remain))
                cs = int(round((remain - total) * 100))
            else:
                total = int(math.ceil(remain - 1e-9)) if remain > 0 else 0
                cs = 0
        else:
            e = self.elapsed()
            total = int(math.floor(e))
            cs = int(round((e - total) * 100))

        total = max(0, total)
        cs = max(0, min(99, cs))

        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        core = "%d:%02d:%02d" % (h, m, s) if h else "%02d:%02d" % (m, s)
        return core, (".%02d" % cs if show_ms else "")

    def is_over_target(self):
        target = self.cfg.get("target") or 0
        if target <= 0 or self.cfg["mode"] == "down":
            return False
        return self.elapsed() > target

    def stats_text(self):
        if not self.cfg.get("show_stats", True):
            return ""
        laps = self.cfg.get("laps") or []
        if not laps:
            hk = self.cfg.get("hotkey_lap") or {}
            if not hk.get("enabled"):
                return ""
            return "按 %s 记一圈" % hotkey_label(int(hk["mods"]), int(hk["vk"]))
        n, avg, best, worst, median = stats_of(laps)
        return "#%d · 上次 %s · 均 %s" % (n, fmt_lap(laps[-1]), fmt_lap(avg))

    # ------------------------------------------------------------ 绘制

    def display_main(self, core):
        if self.running and not self._colon_on:
            return core.replace(":", " ")
        return core

    def _metrics(self, main, frac, sub):
        fm, ff, fs = self.font_main, self.font_frac, self.font_sub
        main_w = fm.measure(main) + (ff.measure(frac) if frac else 0)
        h_main = fm.metrics("linespace")
        h_sub = fs.metrics("linespace") if sub else 0
        gap = int(self.cfg["font_size"] * 0.14) if sub else 0
        sub_w = fs.measure(sub) if sub else 0
        pad_x = int(self.cfg["font_size"] * 0.40) + 12
        pad_y = int(self.cfg["font_size"] * 0.20) + 8
        W = max(24, int(max(main_w, sub_w) + pad_x * 2))
        H = max(24, int(h_main + gap + h_sub + pad_y * 2))
        return W, H, h_main, h_sub, gap, pad_y

    def rebuild(self, force=False):
        core, frac = self.time_parts()
        main = self.display_main(core)
        sub = self.stats_text()

        W, H, h_main, h_sub, gap, pad_y = self._metrics(main, frac, sub)

        if force or W != self.W or H != self.H:
            if self.W > 10:
                cx = self.x + self.W // 2
                cy = self.y + self.H // 2
                nx, ny = cx - W // 2, cy - H // 2
            else:
                nx, ny = self.x, self.y
            self.W, self.H = W, H
            self.x, self.y = nx, ny
            self.root.geometry("%dx%d+%d+%d" % (W, H, self.x, self.y))
            self.canvas.configure(width=W, height=H)
            self._layout_items()
        self._paint(main, frac, sub)
        self._last_sig = (main, frac, sub, self._color_state())

    def _layout_items(self):
        """创建文字项：先描边层，再主体层（保证主体在上）"""
        self.canvas.delete("all")
        self.stroke_items = []
        self.main_items = {}
        self.sub_item = None

        fm, ff, fs = self.font_main, self.font_frac, self.font_sub
        core, frac = self.time_parts()
        main = self.display_main(core)
        sub = self.stats_text()

        main_w = fm.measure(main)
        frac_w = ff.measure(frac) if frac else 0
        total_w = main_w + frac_w
        h_main = fm.metrics("linespace")
        h_sub = fs.metrics("linespace") if sub else 0
        gap = int(self.cfg["font_size"] * 0.14) if h_sub else 0

        pad_y = int(self.cfg["font_size"] * 0.20) + 8
        left = (self.W - total_w) / 2.0
        y_main = pad_y + h_main
        y_frac = y_main - fm.metrics("descent") + ff.metrics("descent")

        fill, outline = THEMES[self.cfg["theme"]]
        ow = max(1, int(round(self.cfg["font_size"] / 62.0)))
        limit = ow * ow + ow

        self._outline_for("main", left, y_main, fm, outline, ow, limit, "sw")
        self.main_items["main"] = self.canvas.create_text(
            left, y_main, text=main, font=fm, fill=fill, anchor="sw"
        )

        if frac:
            self._outline_for("frac", left + main_w, y_frac, ff, outline, ow, limit, "sw")
            self.main_items["frac"] = self.canvas.create_text(
                left + main_w, y_frac, text=frac, font=ff, fill=fill, anchor="sw"
            )

        if h_sub:
            sub_ow = max(1, ow - 1)
            self._outline_for("sub", self.W / 2.0, y_main + gap, fs, outline,
                              sub_ow, sub_ow * sub_ow + sub_ow, "n")
            self.main_items["sub"] = self.canvas.create_text(
                self.W / 2.0, y_main + gap, text=sub, font=fs,
                fill=dim_color(fill, 0.92), anchor="n",
            )
            self.sub_item = self.main_items["sub"]

    def _outline_for(self, key, x, y, font, color, ow, limit, anchor):
        items = []
        for dx in range(-ow, ow + 1):
            for dy in range(-ow, ow + 1):
                if dx == 0 and dy == 0:
                    continue
                if dx * dx + dy * dy > limit:
                    continue
                it = self.canvas.create_text(
                    x + dx, y + dy, text="", font=font, fill=color, anchor=anchor
                )
                items.append(it)
        self.stroke_items.append((items, key))

    def _color_state(self):
        if self._lap_flash_until and time.monotonic() < self._lap_flash_until:
            return "lap"
        if self.is_over_target():
            return "over"
        return "run" if self.running else "idle"

    def _paint(self, main, frac, sub):
        fill, outline = THEMES[self.cfg["theme"]]
        state = self._color_state()
        if state == "lap":
            color = LAP_FLASH_COLOR
        elif state == "over":
            color = OVER_COLOR
        elif state == "run":
            color = fill
        else:
            color = dim_color(fill, 0.85)

        self._set_item("main", main, color)
        if frac:
            self._set_item("frac", frac, color)
        if self.sub_item is not None:
            self._set_item("sub", sub, dim_color(fill, 0.92))

    def _set_item(self, key, text, color):
        it = self.main_items.get(key)
        if it is not None:
            try:
                self.canvas.itemconfigure(it, text=text, fill=color)
            except tk.TclError:
                return
        for items, k in self.stroke_items:
            if k != key:
                continue
            for i in items:
                try:
                    self.canvas.itemconfigure(i, text=text)
                except tk.TclError:
                    pass

    # ------------------------------------------------------------ 计时

    def start(self):
        if self.running:
            return
        if self.cfg["mode"] == "down" and self.cfg["down_total"] - self.elapsed() <= 0:
            self.acc = 0.0
        self.finished = False
        self.t0 = time.monotonic()
        self.running = True
        self._blink_ts = time.monotonic()

    def pause(self):
        if not self.running:
            return
        if self.t0 is not None:
            self.acc += time.monotonic() - self.t0
        self.t0 = None
        self.running = False
        self._colon_on = True

    def toggle(self):
        if self.running:
            self.pause()
        else:
            self.start()

    def reset(self):
        self.running = False
        self.t0 = None
        self.acc = 0.0
        self.finished = False
        self._colon_on = True
        self._lap_flash_until = 0.0
        self.rebuild()

    def tick(self):
        if self.running:
            now = time.monotonic()
            if now - self._blink_ts >= 0.5:
                self._blink_ts = now
                self._colon_on = not self._colon_on
            if self.cfg["mode"] == "down" and self.cfg["down_total"] - self.elapsed() <= 0:
                self.finish()

        if self._lap_flash_until and time.monotonic() >= self._lap_flash_until:
            self._lap_flash_until = 0.0

        core, frac = self.time_parts()
        main = self.display_main(core)
        sub = self.stats_text()
        sig = (main, frac, sub, self._color_state())
        if sig != self._last_sig:
            self._last_sig = sig
            W, H, _, _, _, _ = self._metrics(main, frac, sub)
            if W != self.W or H != self.H:
                self.rebuild()
            else:
                self._paint(main, frac, sub)
        self.root.after(25, self.tick)

    def finish(self):
        self.acc = float(self.cfg["down_total"])
        self.t0 = None
        self.running = False
        self.finished = True
        self._colon_on = True
        play_alert()
        self.flash(0)

    def flash(self, n):
        it = self.main_items.get("main")
        if it is None:
            return
        if n >= 8:
            self._last_sig = None
            return
        color = OVER_COLOR if n % 2 == 0 else THEMES[self.cfg["theme"]][0]
        try:
            self.canvas.itemconfigure(it, fill=color)
        except tk.TclError:
            return
        self.root.after(260, lambda: self.flash(n + 1))

    # ------------------------------------------------------------ 记圈

    def lap(self):
        """记录当前用时，归零并继续下一轮"""
        t = self.elapsed()
        if t < 0.05:
            return
        laps = self.cfg.setdefault("laps", [])
        laps.append(round(t, 2))
        if len(laps) > MAX_LAPS:
            del laps[:-MAX_LAPS]
        save_config(self.cfg)

        was_running = self.running
        self.acc = 0.0
        self.finished = False
        self._colon_on = True
        self.t0 = time.monotonic() if was_running else None
        self._lap_flash_until = time.monotonic() + 0.35
        self.rebuild()

    def undo_lap(self):
        laps = self.cfg.get("laps") or []
        if not laps:
            return
        laps.pop()
        save_config(self.cfg)
        self.rebuild(force=True)

    # ------------------------------------------------------------ 鼠标

    def on_press(self, event):
        self._moved = False
        if self.cfg["locked"]:
            self._drag = None
            return
        self._drag = (event.x_root - self.x, event.y_root - self.y)

    def on_motion(self, event):
        if self._drag is None or self.cfg["locked"]:
            return
        nx = event.x_root - self._drag[0]
        ny = event.y_root - self._drag[1]
        if nx != self.x or ny != self.y:
            self._moved = True
        self.x, self.y = nx, ny
        self.root.geometry("%dx%d+%d+%d" % (self.W, self.H, self.x, self.y))

    def on_release(self, event):
        if self._drag is not None and self._moved:
            self.cfg["x"] = self.x
            self.cfg["y"] = self.y
            save_config(self.cfg)
        self._drag = None

    def on_double(self, event):
        self.toggle()
        return "break"

    def on_wheel(self, event):
        self.change_font(6 if event.delta > 0 else -6)
        return "break"

    def change_font(self, delta):
        new = max(20, min(420, int(self.cfg["font_size"]) + delta))
        if new == self.cfg["font_size"]:
            return
        self.cfg["font_size"] = new
        self._apply_font_size()
        save_config(self.cfg)

    def set_font_size(self, size):
        self.cfg["font_size"] = max(20, min(420, int(size)))
        self._apply_font_size()
        save_config(self.cfg)

    def _apply_font_size(self):
        size = int(self.cfg["font_size"])
        self.font_main.configure(size=size)
        self.font_frac.configure(size=max(12, int(size * 0.56)))
        self.font_sub.configure(size=max(10, min(19, int(size * 0.24))))
        self.rebuild(force=True)

    # ------------------------------------------------------------ 全局热键

    def _init_hotkey(self):
        self._hk = None
        self._hk_toggle_ok = False
        self._hk_lap_ok = False
        self._hk_visible_ok = False
        if sys.platform != "win32":
            return
        try:
            self._hk = HotkeyListener()
            self._hk.start()
            self._hk.ready.wait(2.0)
            if self._hk.error or not self._hk.hwnd:
                self._hk = None
                return
        except Exception:
            self._hk = None
            return
        self.apply_hotkeys()
        self.root.after(50, self._poll_hotkey)

    def _poll_hotkey(self):
        if self._hk is not None:
            try:
                while True:
                    hid = self._hk.queue.get_nowait()
                    if hid == HK_TOGGLE:
                        self.toggle()
                    elif hid == HK_LAP:
                        self.lap()
                    elif hid == HK_VISIBLE:
                        self.toggle_visible()
            except queue.Empty:
                pass
        self.root.after(50, self._poll_hotkey)

    def apply_hotkeys(self):
        if self._hk is None:
            return
        for hid, key, attr in (
            (HK_TOGGLE, "hotkey", "_hk_toggle_ok"),
            (HK_LAP, "hotkey_lap", "_hk_lap_ok"),
        ):
            hk = self.cfg.get(key) or {}
            if hk.get("enabled"):
                ok = self._hk.set_hotkey(hid, int(hk["mods"]), int(hk["vk"]))
                setattr(self, attr, ok)
            else:
                self._hk.clear_hotkey(hid)
                setattr(self, attr, False)

        if self.cfg.get("hotkey_visible", True):
            self._hk_visible_ok = self._hk.set_hotkey(
                HK_VISIBLE, HK_VISIBLE_MODS, HK_VISIBLE_VK
            )
        else:
            self._hk.clear_hotkey(HK_VISIBLE)
            self._hk_visible_ok = False

    def try_hotkey(self, hid, key, mods, vk):
        """尝试注册热键；失败时恢复原设置"""
        if self._hk is None:
            return False
        if self._hk.set_hotkey(hid, mods, vk):
            return True
        old = self.cfg.get(key) or {}
        if old.get("enabled"):
            self._hk.set_hotkey(hid, int(old["mods"]), int(old["vk"]))
        return False

    def toggle_visible(self):
        if self._hidden:
            self.root.deiconify()
            self.root.attributes("-topmost", 1 if self.cfg["topmost"] else 0)
            self._hidden = False
            return
        if not self._hk_visible_ok:
            return
        self.root.withdraw()
        self._hidden = True

    def on_hotkey_enable(self, key, var):
        self.cfg[key]["enabled"] = bool(var.get())
        self.apply_hotkeys()
        save_config(self.cfg)
        self.rebuild(force=True)

    def on_hotkey_visible(self):
        self.cfg["hotkey_visible"] = bool(self.v_hkv.get())
        self.apply_hotkeys()
        save_config(self.cfg)

    def open_hotkey_dialog(self, key="hotkey", title="设置开关热键"):
        if self._hk_dialog is not None:
            try:
                self._hk_dialog.destroy()
            except tk.TclError:
                pass
            self._hk_dialog = None

        hid = HK_TOGGLE if key == "hotkey" else HK_LAP

        d = tk.Toplevel(self.root)
        self._hk_dialog = d
        d.overrideredirect(True)
        d.attributes("-topmost", True)
        d.configure(bg="#20242b", highlightthickness=1,
                    highlightbackground="#3d4653", highlightcolor="#3d4653")

        W, H = 360, 208
        sw, sh = d.winfo_screenwidth(), d.winfo_screenheight()
        x = max(20, min(self.x + self.W // 2 - W // 2, sw - W - 20))
        y = max(20, min(self.y + self.H + 10, sh - H - 40))
        d.geometry("%dx%d+%d+%d" % (W, H, x, y))

        tk.Label(d, text=title, bg="#20242b", fg="#e8ecf1",
                 font=("Microsoft YaHei UI", 10, "bold"), anchor="w").place(x=18, y=12)
        tk.Label(d, text="直接按下想用的组合键（全局生效，任何窗口下都能用）",
                 bg="#20242b", fg="#8b95a3", font=("Microsoft YaHei UI", 8),
                 anchor="w").place(x=18, y=36)

        box = tk.Frame(d, bg="#171a20")
        box.place(x=18, y=58, width=W - 36, height=54)
        preview = tk.Label(box, text="等待按键…", bg="#171a20", fg="#8b95a3",
                           font=("Consolas", 15, "bold"))
        preview.place(relx=0.5, rely=0.5, anchor="center")

        err = tk.Label(d, text="需带 Ctrl / Alt / Shift，或直接用 F1~F12",
                       bg="#20242b", fg="#6b7583",
                       font=("Microsoft YaHei UI", 8), anchor="w")
        err.place(x=18, y=118)

        pending = {"mods": None, "vk": None}

        def on_key(e):
            ks = e.keysym
            if ks in MODIFIER_KEYSYMS:
                preview.configure(text="再按一个主键…", fg="#8b95a3")
                return "break"
            if ks == "Escape":
                close()
                return "break"
            mods = 0
            st = e.state
            if st & 0x0004:
                mods |= MOD_CONTROL
            if st & 0x0008:
                mods |= MOD_ALT
            if st & 0x0001:
                mods |= MOD_SHIFT
            vk = keysym_to_vk(ks, e.keycode)
            if vk is None:
                preview.configure(text="不支持的按键", fg="#ff6b6b")
                pending["mods"], pending["vk"] = None, None
                return "break"
            if not hotkey_valid(mods, vk):
                preview.configure(text="%s  需加 Ctrl/Alt/Shift" % vk_to_label(vk),
                                  fg="#ffb84d")
                pending["mods"], pending["vk"] = None, None
                return "break"
            pending["mods"], pending["vk"] = mods, vk
            preview.configure(text=hotkey_label(mods, vk), fg="#7fd6a6")
            return "break"

        def close(restore=True):
            try:
                d.grab_release()
            except tk.TclError:
                pass
            try:
                d.destroy()
            except tk.TclError:
                pass
            self._hk_dialog = None
            if restore:
                self.apply_hotkeys()

        def ok(_=None):
            if pending["mods"] is None:
                err.configure(text="先按下一个组合键", fg="#ff6b6b")
                return
            if not self.try_hotkey(hid, key, pending["mods"], pending["vk"]):
                err.configure(text="这个组合已被其他程序占用，换一个试试", fg="#ff6b6b")
                return
            self.cfg[key] = {
                "mods": pending["mods"], "vk": pending["vk"], "enabled": True
            }
            save_config(self.cfg)
            close(restore=False)
            self.rebuild(force=True)

        def mkbtn(text, cmd, fg):
            b = tk.Label(d, text=text, bg="#2f3540", fg=fg,
                         font=("Microsoft YaHei UI", 9), cursor="hand2")
            b.bind("<Button-1>", lambda e: cmd())
            b.bind("<Enter>", lambda e: b.configure(bg="#3a414d"))
            b.bind("<Leave>", lambda e: b.configure(bg="#2f3540"))
            return b

        mkbtn("取消", lambda: close(), "#a8b2bf").place(x=18, y=170, width=154, height=26)
        mkbtn("确定", ok, "#7fd6a6").place(x=188, y=170, width=154, height=26)

        d.bind("<KeyPress>", on_key)
        d.bind("<Escape>", lambda e: close())
        try:
            d.grab_set()
        except tk.TclError:
            pass
        d.focus_force()

    # ------------------------------------------------------------ 菜单

    def on_menu(self, event):
        m = self.menu
        m.delete(0, "end")
        self._submenus = []

        hk = self.cfg.get("hotkey") or {}
        hk_lap = self.cfg.get("hotkey_lap") or {}
        toggle_txt = hotkey_label(int(hk.get("mods", 1)), int(hk.get("vk", 0x77)))
        lap_txt = hotkey_label(int(hk_lap.get("mods", 1)), int(hk_lap.get("vk", 0x78)))

        self.v_hk = tk.BooleanVar(master=self.root, value=bool(hk.get("enabled")))
        self.v_hk_lap = tk.BooleanVar(master=self.root, value=bool(hk_lap.get("enabled")))
        self.v_hkv = tk.BooleanVar(
            master=self.root, value=bool(self.cfg.get("hotkey_visible", False))
        )
        self.v_theme = tk.StringVar(master=self.root, value=self.cfg["theme"])
        self.v_ms = tk.BooleanVar(master=self.root, value=bool(self.cfg["show_ms"]))
        self.v_stats = tk.BooleanVar(master=self.root, value=bool(self.cfg["show_stats"]))
        self.v_op = tk.DoubleVar(master=self.root, value=float(self.cfg["opacity"]))
        self.v_top = tk.BooleanVar(master=self.root, value=bool(self.cfg["topmost"]))
        self.v_lock = tk.BooleanVar(master=self.root, value=bool(self.cfg["locked"]))

        m.add_command(label="暂停" if self.running else "开始", command=self.toggle)
        m.add_command(label="记一圈　　%s" % lap_txt, command=self.lap)
        m.add_command(
            label="撤销上一条成绩",
            command=self.undo_lap,
            state="normal" if (self.cfg.get("laps") or []) else "disabled",
        )
        m.add_command(label="重置", command=self.reset)
        m.add_separator()

        mode = tk.Menu(m, tearoff=0, font=("Microsoft YaHei UI", 9))
        self.v_mode = tk.StringVar(master=self.root, value=self.cfg["mode"])
        mode.add_radiobutton(label="正计时", value="up", variable=self.v_mode,
                             command=self.on_mode)
        mode.add_radiobutton(label="倒计时", value="down", variable=self.v_mode,
                             command=self.on_mode)
        self._submenus.append(mode)
        m.add_cascade(label="模式", menu=mode)

        preset = tk.Menu(m, tearoff=0, font=("Microsoft YaHei UI", 9))
        for name, sec in PRESETS:
            preset.add_command(label=name, command=lambda s=sec: self.apply_preset(s))
        preset.add_separator()
        preset.add_command(
            label="自定义…　(%s)" % fmt_duration(self.cfg["down_total"]),
            command=self.open_duration_dialog,
        )
        self._submenus.append(preset)
        m.add_cascade(label="倒计时", menu=preset)

        target = tk.Menu(m, tearoff=0, font=("Microsoft YaHei UI", 9))
        self.v_target = tk.StringVar(
            master=self.root,
            value=("" if not self.cfg.get("target") else "%.1f" % self.cfg["target"]),
        )
        target.add_radiobutton(label="不设目标", value="", variable=self.v_target,
                               command=self.on_target)
        target.add_separator()
        for sec in TARGET_PRESETS:
            target.add_radiobutton(
                label="%d 秒内" % sec, value="%.1f" % sec,
                variable=self.v_target, command=self.on_target,
            )
        target.add_separator()
        target.add_command(label="自定义…", command=self.open_target_dialog)
        self._submenus.append(target)
        m.add_cascade(label="目标用时", menu=target)
        m.add_separator()

        laps = self.cfg.get("laps") or []
        rec = tk.Menu(m, tearoff=0, font=("Microsoft YaHei UI", 9))
        rec.add_command(label="查看成绩记录…　(%d 条)" % len(laps),
                        command=self.open_stats_window)
        rec.add_command(label="导出 CSV…", command=self.export_csv,
                        state="normal" if laps else "disabled")
        rec.add_separator()
        rec.add_command(label="清空记录", command=self.clear_laps,
                        state="normal" if laps else "disabled")
        self._submenus.append(rec)
        m.add_cascade(label="成绩", menu=rec)
        m.add_separator()

        colors = tk.Menu(m, tearoff=0, font=("Microsoft YaHei UI", 9))
        for name in THEMES:
            colors.add_radiobutton(label=name, value=name, variable=self.v_theme,
                                   command=self.on_theme)
        self._submenus.append(colors)
        m.add_cascade(label="颜色", menu=colors)

        size = tk.Menu(m, tearoff=0, font=("Microsoft YaHei UI", 9))
        size.add_command(label="增大", command=lambda: self.change_font(8))
        size.add_command(label="减小", command=lambda: self.change_font(-8))
        size.add_command(label="恢复默认 (70)", command=lambda: self.set_font_size(70))
        self._submenus.append(size)
        m.add_cascade(label="字号", menu=size)

        m.add_checkbutton(label="显示百分秒", variable=self.v_ms, command=self.on_show_ms)
        m.add_checkbutton(label="显示统计行", variable=self.v_stats, command=self.on_show_stats)
        m.add_separator()

        op = tk.Menu(m, tearoff=0, font=("Microsoft YaHei UI", 9))
        for pct in (100, 90, 80, 70, 60, 50, 40):
            op.add_radiobutton(
                label="%d%%" % pct, value=float(pct) / 100.0,
                variable=self.v_op, command=self.on_opacity,
            )
        self._submenus.append(op)
        m.add_cascade(label="不透明度", menu=op)

        m.add_checkbutton(label="总在最前", variable=self.v_top, command=self.on_topmost)
        m.add_checkbutton(label="锁定位置", variable=self.v_lock, command=self.on_lock)

        hk_menu = tk.Menu(m, tearoff=0, font=("Microsoft YaHei UI", 9))
        hk_menu.add_checkbutton(
            label="开始/暂停　%s" % toggle_txt, variable=self.v_hk,
            command=lambda: self.on_hotkey_enable("hotkey", self.v_hk),
        )
        hk_menu.add_checkbutton(
            label="记一圈　%s" % lap_txt, variable=self.v_hk_lap,
            command=lambda: self.on_hotkey_enable("hotkey_lap", self.v_hk_lap),
        )
        hk_menu.add_checkbutton(
            label="显示/隐藏　Ctrl+Alt+H", variable=self.v_hkv,
            command=self.on_hotkey_visible,
        )
        hk_menu.add_separator()
        hk_menu.add_command(
            label="自定义开始/暂停键…",
            command=lambda: self.open_hotkey_dialog("hotkey", "设置开始/暂停热键"),
        )
        hk_menu.add_command(
            label="自定义记圈键…",
            command=lambda: self.open_hotkey_dialog("hotkey_lap", "设置记圈热键"),
        )
        self._submenus.append(hk_menu)
        m.add_cascade(label="快捷键", menu=hk_menu)

        m.add_command(
            label="开机自启" + ("　✓ 已开启" if autostart_enabled() else ""),
            command=self.on_autostart,
        )
        m.add_separator()
        m.add_command(label="帮助 / 操作说明", command=self.show_hint)
        m.add_command(label="退出", command=self.quit)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        mx = max(0, min(event.x_root, sw - 320))
        my = max(0, min(event.y_root, sh - 470))
        try:
            m.tk_popup(mx, my)
        finally:
            m.grab_release()
        return "break"

    def on_mode(self):
        val = self.v_mode.get()
        if val != self.cfg["mode"]:
            self.cfg["mode"] = val
            self.reset()
            save_config(self.cfg)

    def on_theme(self):
        self.cfg["theme"] = self.v_theme.get()
        if self.cfg["theme"] in THEMES:
            self.rebuild(force=True)
            save_config(self.cfg)

    def on_show_ms(self):
        self.cfg["show_ms"] = bool(self.v_ms.get())
        self.rebuild(force=True)
        save_config(self.cfg)

    def on_show_stats(self):
        self.cfg["show_stats"] = bool(self.v_stats.get())
        self.rebuild(force=True)
        save_config(self.cfg)

    def on_opacity(self):
        self.cfg["opacity"] = float(self.v_op.get())
        try:
            self.root.attributes("-alpha", self.cfg["opacity"])
        except tk.TclError:
            pass
        save_config(self.cfg)

    def on_topmost(self):
        self.cfg["topmost"] = bool(self.v_top.get())
        self.root.attributes("-topmost", 1 if self.cfg["topmost"] else 0)
        save_config(self.cfg)

    def on_lock(self):
        self.cfg["locked"] = bool(self.v_lock.get())
        save_config(self.cfg)

    def on_target(self):
        val = self.v_target.get()
        try:
            self.cfg["target"] = max(0.0, float(val)) if val else 0.0
        except ValueError:
            self.cfg["target"] = 0.0
        save_config(self.cfg)
        self.rebuild(force=True)

    def on_autostart(self):
        set_autostart(not autostart_enabled())

    def apply_preset(self, seconds):
        self.cfg["down_total"] = float(seconds)
        self.cfg["mode"] = "down"
        self.reset()
        save_config(self.cfg)

    def clear_laps(self):
        self.cfg["laps"] = []
        save_config(self.cfg)
        self.rebuild(force=True)
        if self._stats_win is not None:
            try:
                self._stats_win.destroy()
            except tk.TclError:
                pass
            self._stats_win = None

    # ------------------------------------------------------------ 成绩窗口

    def open_stats_window(self):
        if self._stats_win is not None:
            try:
                self._stats_win.destroy()
            except tk.TclError:
                pass
            self._stats_win = None

        laps = self.cfg.get("laps") or []
        w = tk.Toplevel(self.root)
        self._stats_win = w
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg="#1b1f26", highlightthickness=1,
                    highlightbackground="#3d4653", highlightcolor="#3d4653")

        W, H = 380, 420
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        x = max(20, min(self.x + self.W // 2 - W // 2, sw - W - 20))
        y = max(20, min(self.y + self.H + 10, sh - H - 40))
        w.geometry("%dx%d+%d+%d" % (W, H, x, y))

        tk.Label(w, text="成绩记录", bg="#1b1f26", fg="#ffffff",
                 font=("Microsoft YaHei UI", 11, "bold"), anchor="w").place(x=18, y=14)

        n, avg, best, worst, median = stats_of(laps)
        if n:
            summary = "共 %d 次　平均 %s　最快 %s　最慢 %s　中位 %s" % (
                n, fmt_lap(avg), fmt_lap(best), fmt_lap(worst), fmt_lap(median)
            )
        else:
            summary = "还没有记录，按记圈键开始"
        tk.Label(w, text=summary, bg="#1b1f26", fg="#8b95a3",
                 font=("Microsoft YaHei UI", 8), anchor="w").place(x=18, y=42)

        body_h = H - 68 - 56
        body = tk.Text(w, bg="#14171c", fg="#c8d2df", bd=0, highlightthickness=0,
                       font=("Consolas", 10), padx=10, pady=8, wrap="none")
        body.place(x=18, y=68, width=W - 36 - 10, height=body_h)

        scroll = tk.Scrollbar(w, command=body.yview, width=8)
        scroll.place(x=W - 18 - 8, y=68, height=body_h)
        body.configure(yscrollcommand=scroll.set)

        for i, v in enumerate(laps, 1):
            diff = v - avg
            tag = "best" if abs(v - best) < 1e-9 else ("worst" if abs(v - worst) < 1e-9 else "")
            body.insert("end", "%3d   %8s   %+7.2fs\n" % (i, fmt_lap(v), diff), tag)
        body.tag_configure("best", foreground="#5BE49B")
        body.tag_configure("worst", foreground="#FF7A7D")
        body.configure(state="disabled")
        body.see("end")

        def close(_=None):
            try:
                w.destroy()
            except tk.TclError:
                pass
            self._stats_win = None

        def mkbtn(text, cmd, fg, bg="#2f3540"):
            b = tk.Label(w, text=text, bg=bg, fg=fg,
                         font=("Microsoft YaHei UI", 9), cursor="hand2")
            b.bind("<Button-1>", lambda e: cmd())
            b.bind("<Enter>", lambda e: b.configure(bg="#3a414d"))
            b.bind("<Leave>", lambda e: b.configure(bg=bg))
            return b

        by = H - 42
        mkbtn("导出 CSV", self.export_csv, "#7fd6a6").place(x=18, y=by, width=110, height=28)
        mkbtn("清空", self.clear_laps, "#ff8a8a").place(x=138, y=by, width=90, height=28)
        mkbtn("关闭", close, "#a8b2bf").place(x=W - 18 - 110, y=by, width=110, height=28)

        w.bind("<Escape>", close)

    def export_csv(self):
        laps = self.cfg.get("laps") or []
        if not laps:
            return
        from tkinter import filedialog

        default = "timer_laps_%s.csv" % time.strftime("%Y%m%d_%H%M")
        try:
            path = filedialog.asksaveasfilename(
                parent=self.root,
                title="导出成绩",
                defaultextension=".csv",
                initialfile=default,
                filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
            )
        except Exception:
            return
        if not path:
            return

        n, avg, best, worst, median = stats_of(laps)
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fp:
                writer = csv.writer(fp)
                writer.writerow(["序号", "用时(秒)", "相对平均(秒)"])
                for i, v in enumerate(laps, 1):
                    writer.writerow([i, "%.2f" % v, "%+.2f" % (v - avg)])
                writer.writerow([])
                writer.writerow(["统计", "数值"])
                writer.writerow(["次数", n])
                writer.writerow(["平均", "%.2f" % avg])
                writer.writerow(["最快", "%.2f" % best])
                writer.writerow(["最慢", "%.2f" % worst])
                writer.writerow(["中位数", "%.2f" % median])
                writer.writerow(["导出时间", time.strftime("%Y-%m-%d %H:%M:%S")])
        except Exception:
            return

        self.toast("已导出到 %s" % os.path.basename(path))

    def toast(self, text):
        t = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.configure(bg="#20242b", highlightthickness=1,
                    highlightbackground="#3d4653", highlightcolor="#3d4653")
        tk.Label(t, text=text, bg="#20242b", fg="#c8d2df",
                 font=("Microsoft YaHei UI", 9), padx=16, pady=10).pack()
        t.update_idletasks()
        tw, th = t.winfo_reqwidth(), t.winfo_reqheight()
        sw, sh = t.winfo_screenwidth(), t.winfo_screenheight()
        t.geometry("%dx%d+%d+%d" % (tw, th, (sw - tw) // 2, sh - th - 90))
        t.after(2600, t.destroy)

    # ------------------------------------------------------------ 对话框

    def _dialog_shell(self, title, subtitle, height=138):
        if self._dialog is not None:
            try:
                self._dialog.destroy()
            except tk.TclError:
                pass
            self._dialog = None

        d = tk.Toplevel(self.root)
        self._dialog = d
        d.overrideredirect(True)
        d.attributes("-topmost", True)
        d.configure(bg="#20242b", highlightthickness=1,
                    highlightbackground="#3d4653", highlightcolor="#3d4653")

        W, H = 300, height
        sw, sh = d.winfo_screenwidth(), d.winfo_screenheight()
        x = max(20, min(self.x + self.W // 2 - W // 2, sw - W - 20))
        y = max(20, min(self.y + self.H + 10, sh - H - 40))
        d.geometry("%dx%d+%d+%d" % (W, H, x, y))

        tk.Label(d, text=title, bg="#20242b", fg="#e8ecf1",
                 font=("Microsoft YaHei UI", 10, "bold"), anchor="w").place(x=18, y=12)
        tk.Label(d, text=subtitle, bg="#20242b", fg="#8b95a3",
                 font=("Microsoft YaHei UI", 8), anchor="w").place(x=18, y=36)
        return d

    def _dialog_buttons(self, d, on_ok, on_cancel, y):
        def mkbtn(text, cmd, fg):
            b = tk.Label(d, text=text, bg="#2f3540", fg=fg,
                         font=("Microsoft YaHei UI", 9), cursor="hand2")
            b.bind("<Button-1>", lambda e: cmd())
            b.bind("<Enter>", lambda e: b.configure(bg="#3a414d"))
            b.bind("<Leave>", lambda e: b.configure(bg="#2f3540"))
            return b

        mkbtn("取消", on_cancel, "#a8b2bf").place(x=18, y=y, width=124, height=26)
        mkbtn("确定", on_ok, "#7fd6a6").place(x=158, y=y, width=124, height=26)

    def open_duration_dialog(self):
        d = self._dialog_shell(
            "设置倒计时时长",
            "25 = 25 分钟    5:00 = 5 分    1:30:00 = 1.5 小时",
        )

        entry = tk.Entry(d, bg="#2b3038", fg="#ffffff", insertbackground="#ffffff",
                         relief="flat", font=("Consolas", 13), justify="center")
        entry.place(x=18, y=58, width=264, height=32)
        entry.insert(0, fmt_duration(self.cfg["down_total"]))
        entry.select_range(0, "end")
        entry.focus_force()

        err = tk.Label(d, text="", bg="#20242b", fg="#ff6b6b",
                       font=("Microsoft YaHei UI", 8), anchor="w")
        err.place(x=18, y=93)

        def close(_=None):
            try:
                d.destroy()
            except tk.TclError:
                pass
            self._dialog = None

        def ok(_=None):
            sec = parse_duration(entry.get())
            if sec is None or sec <= 0:
                err.configure(text="格式不对，试试 25 或 5:00 或 1:30:00")
                return
            self.apply_preset(sec)
            close()

        self._dialog_buttons(d, ok, close, 104)
        d.bind("<Return>", ok)
        d.bind("<Escape>", close)
        entry.bind("<Return>", ok)
        entry.bind("<Escape>", close)

    def open_target_dialog(self):
        d = self._dialog_shell(
            "目标用时（超过后数字变红）",
            "填秒数，例如 12 表示 12 秒内；留空或 0 取消目标",
        )

        entry = tk.Entry(d, bg="#2b3038", fg="#ffffff", insertbackground="#ffffff",
                         relief="flat", font=("Consolas", 13), justify="center")
        entry.place(x=18, y=58, width=264, height=32)
        if self.cfg.get("target"):
            entry.insert(0, "%g" % self.cfg["target"])
        entry.focus_force()

        err = tk.Label(d, text="", bg="#20242b", fg="#ff6b6b",
                       font=("Microsoft YaHei UI", 8), anchor="w")
        err.place(x=18, y=93)

        def close(_=None):
            try:
                d.destroy()
            except tk.TclError:
                pass
            self._dialog = None

        def ok(_=None):
            raw = entry.get().strip()
            if not raw:
                self.cfg["target"] = 0.0
                save_config(self.cfg)
                self.rebuild(force=True)
                close()
                return
            try:
                val = float(raw)
            except ValueError:
                err.configure(text="请输入数字，例如 12")
                return
            if val < 0:
                err.configure(text="不能是负数")
                return
            self.cfg["target"] = val
            save_config(self.cfg)
            self.rebuild(force=True)
            close()

        self._dialog_buttons(d, ok, close, 104)
        d.bind("<Return>", ok)
        d.bind("<Escape>", close)
        entry.bind("<Return>", ok)
        entry.bind("<Escape>", close)

    def show_hint(self):
        t = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.configure(bg="#1b1f26", highlightthickness=1,
                    highlightbackground="#3d4653", highlightcolor="#3d4653")

        hk = self.cfg.get("hotkey") or {}
        hk_lap = self.cfg.get("hotkey_lap") or {}
        toggle_txt = hotkey_label(int(hk.get("mods", 1)), int(hk.get("vk", 0x77)))
        lap_txt = hotkey_label(int(hk_lap.get("mods", 1)), int(hk_lap.get("vk", 0x78)))

        rows = [
            ("双击数字", "开始 / 暂停"),
            (toggle_txt, "全局开始 / 暂停"),
            (lap_txt, "记一圈，归零进入下一轮"),
            ("滚轮", "放大 / 缩小"),
            ("左键拖动", "移动位置"),
            ("右键", "模式 · 成绩 · 颜色 · 设置"),
        ]

        W, H = 350, 232
        sw, sh = t.winfo_screenwidth(), t.winfo_screenheight()
        t.geometry("%dx%d+%d+%d" % (W, H, (sw - W) // 2, (sh - H) // 2 - 120))

        tk.Label(t, text="桌面透明计时器", bg="#1b1f26", fg="#ffffff",
                 font=("Microsoft YaHei UI", 11, "bold")).place(x=20, y=14)

        y = 48
        for k, v in rows:
            tk.Label(t, text=k, bg="#1b1f26", fg="#7fd6a6",
                     font=("Microsoft YaHei UI", 9, "bold"), anchor="w").place(x=20, y=y)
            tk.Label(t, text=v, bg="#1b1f26", fg="#c3ccd8",
                     font=("Microsoft YaHei UI", 9), anchor="w").place(x=128, y=y)
            y += 26

        tip = tk.Label(t, text="点击关闭", bg="#1b1f26", fg="#5b6675",
                       font=("Microsoft YaHei UI", 8))
        tip.place(x=20, y=H - 24)

        def close(_=None):
            try:
                t.destroy()
            except tk.TclError:
                pass

        for widget in (t, tip):
            widget.bind("<Button-1>", close)
        t.bind("<Escape>", close)
        t.after(9000, close)

    def quit(self):
        self.cfg["x"] = self.x
        self.cfg["y"] = self.y
        save_config(self.cfg)
        if self._hk is not None:
            try:
                self._hk.stop()
            except Exception:
                pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def main():
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    root = tk.Tk()
    TimerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
