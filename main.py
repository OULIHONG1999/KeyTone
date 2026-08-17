# KeyTone / 键响匣 幽灵版
# ============================================================
# 开启后找不到关闭的地方：无窗口、无控制台、无托盘图标、
# 无任何提示、无退出快捷键，只能通过任务管理器结束进程。
# 双击启动弹自定义轻量 toast；已运行时重复启动弹 toast 提醒。
# 无需管理员权限（pynput 低级钩子普通权限即可全局监听，
# 仅监听不到"以管理员身份运行"的窗口内的按键）。
# 排障出口：同目录 keytone.log（仅用于排查，可随时删除）。
# 启动：双击 start_ghost.vbs，或命令行 pythonw main.py
# ============================================================
import array
import ctypes
import json
import logging
import math
import os
import sys
import threading
import tkinter as tk
import winreg

import pygame
from pynput import keyboard

# 程序所在目录：打包后为 exe 所在目录（日志不写进 PyInstaller 临时解压目录）
APP_DIR = os.path.dirname(os.path.abspath(
    sys.executable if getattr(sys, "frozen", False) else __file__))
LOG_FILE = os.path.join(APP_DIR, "keytone.log")
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("keytone")


def resource_path(rel):
    """解析资源路径：PyInstaller 打包后资源在 _MEIPASS，源码运行在项目目录。"""
    base = getattr(sys, "_MEIPASS", APP_DIR)
    return os.path.join(base, rel)

# 初始化音频（显式单声道 16bit，便于变调重采样）
SAMPLE_RATE = 44100
try:
    pygame.mixer.init(frequency=SAMPLE_RATE, size=-16, channels=1)
except Exception as e:
    log.error("pygame.mixer.init 失败: %s", e)
    raise
# 单通道播放：新按键音立即打断正在播放的音，避免叠加
pygame.mixer.set_num_channels(1)

# ==========按键-音效映射，自行替换wav文件路径==========
KEY_SOUND_MAP = {
    "a": "sounds/a.wav",
    "b": "sounds/b.wav",
    "space": "sounds/space.wav",
    "enter": "sounds/enter.wav",
    "esc": "sounds/esc.wav",
}

# 未映射按键的默认音效，保证每个键按下都有声音
DEFAULT_SOUND = "sounds/default.wav"

# 配置文件（exe/源码同目录）+ 默认配置
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
# 内置预制音乐（曲名 → 简谱；' 高八度、. 低八度、0 休止、空格分组）
MELODIES_DEFAULT = {
    "小星星": "1155665 4433221 5544332 5544332 1155665 4433221",
    "两只老虎": "1231 1231 345 345 565431 565431 151 151",
    "生日快乐": "55651'7 55652'1' 555'3'1'76 443'1'2'1'",
    "欢乐颂": "33455432 1123322 33455432 1123211",
    "粉刷匠": "5353531 24325 5353531 24321",
    "铃儿响叮当": "333 333 351'2'3' 4'4'3'3'3' 2'2'3'2'5 333 333 351'2'3' 4'4'3'3'3' 2'2'3'2'1",
}

DEFAULT_CONFIG = {
    "sound_map": dict(KEY_SOUND_MAP),
    "default_sound": DEFAULT_SOUND,
    "melodies": MELODIES_DEFAULT,
    "melody_index": 0,
    "volume": 0.5,
    "pitch": 1.0,
}


def load_config():
    """读取 config.json；缺失/损坏时回退默认值并写回一份。"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            user = json.load(f)
        for key in cfg:
            if key in user:
                cfg[key] = user[key]
    except FileNotFoundError:
        save_config(cfg)
    except Exception as e:
        log.warning("配置读取失败，使用默认值: %s", e)
    return cfg


def save_config(cfg):
    """写入 config.json（保留用户可读格式）。"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning("配置写入失败: %s", e)

# 音调调节范围 / 步进
PITCH_MIN, PITCH_MAX = 0.5, 2.0
ADJUST_STEP = 0.05

# 单实例互斥体名（Local 命名空间，同一用户会话内唯一）
MUTEX_NAME = "Local\\KeyTone_SingleInstance"
# 互斥体句柄：进程生命周期内保持引用，防止被回收后失去锁
_mutex_handle = None


class SoundEngine:
    """按键音引擎：预加载全部音效，支持全局音量与音调调节。

    后续接入 GUI / 外部控制时，直接调用 set_volume / set_pitch / play 即可。
    """

    def __init__(self, sound_map, default_sound):
        self._sounds = {}     # 名称 -> 原始 Sound
        self._raw = {}        # 名称 -> 原始采样 array('h')
        self._cache = {}      # (名称, 音调) -> 变调后的 Sound
        self._default = "__default__"
        self._volume = 0.5
        self._pitch = 1.0
        for name, path in sound_map.items():
            self._load(name, path)
        self._load(self._default, default_sound)

    @property
    def volume(self):
        return self._volume

    @property
    def pitch(self):
        return self._pitch

    @staticmethod
    def _resolve_sound_path(path):
        """解析音效路径：绝对路径直接用；相对路径先查程序目录（外部音效），再查打包资源。"""
        if os.path.isabs(path):
            return path
        external = os.path.join(APP_DIR, path)
        if os.path.exists(external):
            return external
        return resource_path(path)

    def _load(self, name, path):
        full = self._resolve_sound_path(path)
        try:
            sound = pygame.mixer.Sound(full)
        except Exception as e:
            log.warning("音效加载失败 %s: %s", path, e)
            return
        self._sounds[name] = sound
        self._raw[name] = array.array("h", sound.get_raw())
        sound.set_volume(self._volume)

    def _resample(self, samples, rate):
        """线性插值重采样：rate>1 音调变高（时长变短），rate<1 变低。"""
        out_len = int(len(samples) / rate)
        if out_len <= 0:
            return array.array("h")
        out = []
        pos = 0.0
        step = 1.0 / rate
        last = len(samples) - 1
        for _ in range(out_len):
            i0 = int(pos)
            if i0 >= last:  # 降调时输出长于输入，末尾保持最后一个采样
                i0, i1, frac = last, last, 0.0
            else:
                i1 = i0 + 1
                frac = pos - i0
            out.append(int(samples[i0] * (1.0 - frac) + samples[i1] * frac))
            pos += step
        return array.array("h", out)

    def _get_sound(self, name):
        """按当前音调取 Sound（首次变调时生成并缓存）。"""
        pitch = round(self._pitch, 3)
        key = (name, pitch)
        snd = self._cache.get(key)
        if snd is None:
            samples = self._raw[name]
            if abs(pitch - 1.0) > 0.001:
                samples = self._resample(samples, pitch)
            snd = pygame.mixer.Sound(buffer=samples.tobytes())
            snd.set_volume(self._volume)
            self._cache[key] = snd
        return snd

    def set_volume(self, vol):
        self._volume = max(0.0, min(1.0, vol))

    def set_pitch(self, mult):
        self._pitch = max(PITCH_MIN, min(PITCH_MAX, mult))

    def reload_map(self, sound_map):
        """热重载按键音效映射（F11 切换方案），默认音始终保留。"""
        for name in [n for n in self._sounds if n != self._default]:
            if name not in sound_map:
                del self._sounds[name]
                del self._raw[name]
        for name, path in sound_map.items():
            if name not in self._sounds:
                self._load(name, path)

    def play(self, name):
        """播放按键音；未映射的按键回退到默认音（两者都缺失则静默放弃）。"""
        if name not in self._sounds:
            name = self._default
        if name not in self._sounds:
            return
        snd = self._get_sound(name)
        snd.set_volume(self._volume)
        _play_interrupt(snd)


# 全局配置 + 音效引擎（模块级实例，供回调使用）
CONFIG = load_config()
engine = SoundEngine(CONFIG["sound_map"], CONFIG["default_sound"])
engine.set_volume(CONFIG["volume"])
engine.set_pitch(CONFIG["pitch"])

# ========== 音符弹琴模式：任意按键按下都发一个电子琴音 ==========
# 模式：0=按键音效，1=音符弹琴，2=预制旋律；F12 循环切换（本身不发声）
MODE_SOUND, MODE_NOTE, MODE_MELODY = 0, 1, 2
MODE_NAMES = ("按键音效模式", "音符弹琴模式", "预制旋律模式")
# 各模式的按键功能提示（toast 显示）
MODE_HINTS = {
    MODE_SOUND: ("模式 按键音效", "F11 切音效方案"),
    MODE_NOTE: ("模式 音符弹琴", "F11 切音色"),
    MODE_MELODY: ("模式 预制旋律", "F11 切曲目"),
}
MODE_TOGGLE_KEY = "f12"

# 键盘布局 → 音高：主键盘区按行（行越高音越高，行内左低右高），
# 顺序表按行低→高展开（序号递增 = 音高递增）；特殊键排低音区，F1~F11 排最高音区
NOTE_ROWS = [
    "zxcvbnm,./",      # 字母下行（最低）
    "asdfghjkl;'",     # 字母中行
    "qwertyuiop[]\\",  # 字母上行
    "`1234567890-=",   # 数字行（最高）
]
NOTE_SPECIAL_LOW = ["esc", "tab", "caps_lock", "shift", "ctrl", "alt", "space",
                    "enter", "backspace", "delete", "insert", "home", "end",
                    "pgup", "pgdn", "up", "down", "left", "right",
                    "print_screen", "scroll_lock", "pause", "num_lock", "menu", "cmd"]
NOTE_SPECIAL_HIGH = ["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11"]
# 顺序表：低音特殊键 + 主键盘（行高→低）+ 高音F键，序号 = 半音偏移
NOTE_SEQUENCE = NOTE_SPECIAL_LOW + [c for row in NOTE_ROWS for c in row] + NOTE_SPECIAL_HIGH
NOTE_BASE_MIDI = 45  # A2，最低音


def _midi_of(char):
    """按键字符 → MIDI 音高编号；不在布局内返回 None（静默）。"""
    try:
        idx = NOTE_SEQUENCE.index(char)
    except ValueError:
        return None
    return NOTE_BASE_MIDI + idx


class NoteEngine:
    """合成电子琴音引擎：任意按键都发一个音符，lazy 合成 + 缓存。"""

    def __init__(self):
        self._cache = {}
        self._wave = 0  # 0=电子琴 1=正弦 2=方波

    def set_wave(self, wave):
        self._wave = wave

    def _synth(self, midi):
        """按当前音色合成音符（0.4s，指数衰减）。"""
        freq = 440.0 * (2.0 ** ((midi - 69) / 12.0))
        n = int(SAMPLE_RATE * 0.4)
        # 按 mixer 实际声道数生成交错数据（SDL 可能把 mono 请求改写为 stereo）
        _, _, channels = pygame.mixer.get_init()
        frames = []
        for i in range(n):
            t = i / SAMPLE_RATE
            env = math.exp(-8.0 * t)
            if self._wave == 1:  # 纯正弦（柔和）
                v = math.sin(2.0 * math.pi * freq * t)
            elif self._wave == 2:  # 方波（复古）
                v = 1.0 if math.sin(2.0 * math.pi * freq * t) >= 0 else -1.0
            else:  # 电子琴（默认）
                v = (math.sin(2.0 * math.pi * freq * t)
                     + 0.5 * math.sin(4.0 * math.pi * freq * t)
                     + 0.25 * math.sin(6.0 * math.pi * freq * t)) / 1.75
            s = int(32767 * env * v)
            frames.append(s)
            if channels == 2:
                frames.append(s)  # 复制为 L/R 对
        return pygame.mixer.Sound(buffer=array.array("h", frames).tobytes())

    def play_midi(self, midi):
        """按 MIDI 编号播放音符（共享缓存，按音色隔离）；midi 为 None 时静默。"""
        if midi is None:
            return
        key = (self._wave, midi)
        snd = self._cache.get(key)
        if snd is None:
            snd = self._synth(midi)
            self._cache[key] = snd
        snd.set_volume(engine.volume * 0.7)  # 跟随全局音量
        _play_interrupt(snd)

    def play(self, char):
        """播放按键对应的音符；不在布局内的按键静默。"""
        self.play_midi(_midi_of(char))


def _solfa_to_midi(note):
    """简谱音名 → MIDI 编号；休止符/无法识别返回 None。"""
    base = {"1": 60, "2": 62, "3": 64, "4": 65, "5": 67, "5'": 79, "6": 69, "7": 71}
    oct_shift = note.count("'") - note.count(".")
    d = note.strip("'.")
    if d == "0" or d not in base:
        return None
    return base[d] + 12 * oct_shift


class MelodyEngine:
    """预制旋律引擎：多首曲目，F11 切换；不管按哪个键都按当前曲目顺序播放下一个音。"""

    def __init__(self, melodies):
        self._melodies = dict(melodies)
        self._names = list(melodies)
        self._name = self._names[0] if self._names else ""
        self._notes = [c for c in self._melodies.get(self._name, "") if c != " "]
        self._pos = 0

    @property
    def name(self):
        return self._name

    def switch_next(self):
        """切到下一首曲目（循环），返回新曲名。"""
        if not self._names:
            return ""
        idx = (self._names.index(self._name) + 1) % len(self._names)
        self._name = self._names[idx]
        self._notes = [c for c in self._melodies[self._name] if c != " "]
        self._pos = 0
        return self._name

    def play(self):
        """播放当前位置的音符并推进游标（休止符无声推进）。"""
        if not self._notes:
            return
        midi = _solfa_to_midi(self._notes[self._pos])
        self._pos = (self._pos + 1) % len(self._notes)
        note_engine.play_midi(midi)


# 音符引擎实例 + 当前模式
note_engine = NoteEngine()
melody_engine = MelodyEngine(CONFIG["melodies"])
# 应用配置选中的曲目
for _ in range(CONFIG.get("melody_index", 0) % max(len(melody_engine._names), 1)):
    melody_engine.switch_next()
MODE = MODE_SOUND


def switch_melody():
    """切到下一首曲目（F11），toast 提示并写回配置。"""
    name = melody_engine.switch_next()
    if name:
        CONFIG["melody_index"] = melody_engine._names.index(name)
        save_config(CONFIG)
    _toast("KeyTone 键响匣", f"曲目 {name}" if name else "曲目 暂无", "F11 下一首")


# F11 多用途：按键音效切方案 / 音符弹琴切音色 / 旋律切曲目
SOUND_PROFILES = {
    "用户配置": CONFIG["sound_map"],
    "内置默认": dict(KEY_SOUND_MAP),
}
PROFILE_NAMES = list(SOUND_PROFILES)
_profile_index = 0  # 初始为用户配置（engine 默认即用户映射）
WAVE_NAMES = ("电子琴", "正弦", "方波")
_wave_index = 0


def switch_sound_profile():
    """F11：切换按键音效方案（用户配置 ↔ 内置默认）。"""
    global _profile_index
    _profile_index = (_profile_index + 1) % len(PROFILE_NAMES)
    engine.reload_map(SOUND_PROFILES[PROFILE_NAMES[_profile_index]])
    _toast("KeyTone 键响匣", f"音效 {PROFILE_NAMES[_profile_index]}", "F11 切换方案")


def switch_note_wave():
    """F11：切换音符弹琴音色（电子琴/正弦/方波）。"""
    global _wave_index
    _wave_index = (_wave_index + 1) % len(WAVE_NAMES)
    note_engine.set_wave(_wave_index)
    _toast("KeyTone 键响匣", f"音色 {WAVE_NAMES[_wave_index]}", "F11 切换音色")


def toggle_mode():
    """循环切换三种模式（F12），toast 显示模式与按键功能。"""
    global MODE
    MODE = (MODE + 1) % 3
    msg, hint = MODE_HINTS[MODE]
    _toast("KeyTone 键响匣", msg, hint)
    log.info("模式切换: %s", MODE_NAMES[MODE])

def _play_interrupt(snd):
    """打断式播放：先停止所有正在播放的音，再播新音（新按键音打断旧音）。"""
    pygame.mixer.stop()
    snd.play()


# 控制键：= / - 调音量，] / [ 调音调（静默调节，按键本身不发声）
# 调节键：F9 音量- / F10 音量+（功能键，不影响打字输入）
CONTROL_KEYS = {
    "f9": "vol_down",
    "f10": "vol_up",
}

# 记录已经按下的按键，防止长按重复播放
pressed_set = set()


def handle_control(action):
    if action == "vol_up":
        engine.set_volume(engine.volume + ADJUST_STEP)
    elif action == "vol_down":
        engine.set_volume(engine.volume - ADJUST_STEP)
    elif action == "pitch_up":
        engine.set_pitch(engine.pitch + ADJUST_STEP)
    elif action == "pitch_down":
        engine.set_pitch(engine.pitch - ADJUST_STEP)
    # 调节后写回配置，重启保留
    CONFIG["volume"] = engine.volume
    CONFIG["pitch"] = engine.pitch
    save_config(CONFIG)
    _toast("KeyTone 键响匣", f"调节 音量 {engine.volume:.2f}  音调 {engine.pitch:.2f}", "F9 音量-  F10 音量+")


# ========== toast：单 Tk 主线程架构 ==========
# 多线程各自创建 Tk 会导致 Tcl 解释器冲突崩溃（Tcl 非线程安全），
# 改为：模块级隐藏根窗口 + 所有 toast 由主线程 mainloop/update 驱动
_tk_root = tk.Tk()
_tk_root.withdraw()

# 堆叠式 toast：活跃窗口列表（从下到上）+ 尺寸常量
_toast_windows = []
TOAST_W, TOAST_H, TOAST_GAP = 300, 104, 12
TOAST_BOTTOM = 80


def _system_dark_theme():
    """读取 Windows 系统深浅色主题；失败默认深色。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return val == 0  # 0 = 深色
    except OSError:
        return True


def _system_accent_color():
    """读取 Windows 系统强调色（AccentColor，ABGR 格式）；失败回退默认蓝。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\DWM") as key:
            val, _ = winreg.QueryValueEx(key, "AccentColor")
        b, g, r = val & 0xFF, (val >> 8) & 0xFF, (val >> 16) & 0xFF
        return f"#{r:02x}{g:02x}{b:02x}"
    except OSError:
        return "#3b9eff"


def _slide_to(w, tx, ty, steps=6, delay=20):
    """平滑移动窗口到目标位置（主线程 after 链）。"""
    x0, y0 = w.winfo_x(), w.winfo_y()
    if (x0, y0) == (tx, ty):
        return
    dx, dy = (tx - x0) / steps, (ty - y0) / steps
    def step(i=0):
        if i >= steps:
            w.geometry(f"{TOAST_W}x{TOAST_H}+{tx}+{ty}")
            return
        w.geometry(f"{TOAST_W}x{TOAST_H}+{int(x0 + dx * i)}+{int(y0 + dy * i)}")
        w.after(delay, lambda: step(i + 1))
    step()


def _reflow_toasts():
    """销毁后重排：剩余 toast 从底部向上依次回位。"""
    sh = _tk_root.winfo_screenheight()
    y = sh - TOAST_H - TOAST_BOTTOM
    for w in reversed(_toast_windows):
        _slide_to(w, w.winfo_x(), y)
        y -= TOAST_H + TOAST_GAP


def _close_toast(win, quit_on_close):
    """销毁 toast、移出列表、重排剩余窗口（wait 场景同时 quit）。"""
    if win in _toast_windows:
        _toast_windows.remove(win)
    win.destroy()
    if quit_on_close:
        win.quit()
    _reflow_toasts()


# Windows 磨砂玻璃（Acrylic）支持：SetWindowCompositionAttribute
if sys.platform == "win32":
    class _ACCENT_POLICY(ctypes.Structure):
        _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                    ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]

    class _WCA_DATA(ctypes.Structure):
        _fields_ = [("Attrib", ctypes.c_int), ("Data", ctypes.c_void_p),
                    ("SizeOfData", ctypes.c_size_t)]


def _apply_acrylic(hwnd, gradient_color=0xAA1F1F1F):
    """启用 Windows Acrylic 磨砂背景；成功返回 True，失败返回 False。"""
    try:
        if sys.platform != "win32":
            return False
        accent = _ACCENT_POLICY(4, 2, gradient_color, 0)  # AccentState=4 磨砂
        data = _WCA_DATA(19, ctypes.addressof(accent), ctypes.sizeof(accent))
        ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
        return True
    except Exception:
        return False


def _apply_round_rect(hwnd, w, h, radius=18):
    """圆角窗口区域：CreateRoundRectRgn + SetWindowRgn。"""
    try:
        if sys.platform != "win32":
            return
        rgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, radius, radius)
        ctypes.windll.user32.SetWindowRgn(hwnd, rgn, True)
    except Exception:
        pass


def _round_rect(canvas, x1, y1, x2, y2, r, **kw):
    """Canvas 圆角矩形（smooth 多边形），fill/outline 沿圆角弧线。"""
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def _build_toast_window(title, message, hint, duration_ms, quit_on_close=False):
    """创建堆叠式 toast：新 toast 淡入出现在底部，旧 toast 被往上推，倒计时淡出销毁。

    自动适配系统深浅色主题；圆角 + 磨砂（Acrylic）+ 半透明背景。
    """
    # 主题色板 + 系统强调色
    accent = _system_accent_color()
    if _system_dark_theme():
        BG, BORDER, SEP = "#202020", "#3a3a3a", "#333333"
        FG_TITLE, FG_MSG, FG_HINT = "#ffffff", "#eeeeee", "#9a9a9a"
        ACCENT = 0xAA202020
    else:
        BG, BORDER, SEP = "#f5f5f5", "#d5d5d5", "#e2e2e2"
        FG_TITLE, FG_MSG, FG_HINT = "#1a1a1a", "#333333", "#888888"
        ACCENT = 0xAAE8E8E8
    hint = hint or "F12/F11 切换  F9/F10 音量"
    win = tk.Toplevel(_tk_root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    x = (sw - TOAST_W) // 2
    win.geometry(f"{TOAST_W}x{TOAST_H}+{x}+{sh}")  # 初始屏幕底部外
    # 极简圆角卡片：无描边（磨砂+半透明分层），文本全部 create_text（无背景、不遮挡）
    canvas = tk.Canvas(win, width=TOAST_W, height=TOAST_H, bg=BG, highlightthickness=0)
    canvas.pack()
    _round_rect(canvas, 0, 0, TOAST_W, TOAST_H, 14, fill=BG)
    # 强调色圆形图标（标题首字符）
    canvas.create_oval(14, 14, 42, 42, fill=accent, outline="")
    canvas.create_text(28, 28, text=title[:1], fill="#ffffff",
                       font=("Microsoft YaHei", 12, "bold"))
    # 标题（图标右侧，与图标垂直居中对齐）
    canvas.create_text(50, 28, text=title, anchor="w", fill=FG_TITLE,
                       font=("Microsoft YaHei", 10, "bold"))
    # 倒计时（右上角，与标题同水平线）
    remaining = [max(1, round(duration_ms / 1000))]
    count_lbl = canvas.create_text(TOAST_W - 14, 28, text=f"{remaining[0]}s",
                                   anchor="e", fill=FG_HINT,
                                   font=("Microsoft YaHei", 9))
    # 分隔线：标题与内容之间
    canvas.create_line(14, 48, TOAST_W - 14, 48, fill=SEP, width=1)
    # 内容行（create_text 自动换行，无背景不遮挡）
    canvas.create_text(50, 56, text=message, anchor="nw", fill=FG_MSG,
                       font=("Microsoft YaHei", 10), width=240, justify="left")
    # 提示行
    canvas.create_text(50, 84, text=f"提示 {hint}", anchor="nw", fill=FG_HINT,
                       font=("Microsoft YaHei", 8), width=240, justify="left")

    # 旧 toast 上移一格（往上推）
    for w in list(_toast_windows):
        _slide_to(w, w.winfo_x(), w.winfo_y() - (TOAST_H + TOAST_GAP))
    _toast_windows.append(win)

    # 视觉：圆角 + 磨砂 + 半透明背景（半透明保证生效）
    win.update_idletasks()
    hwnd = win.winfo_id()  # Toplevel 句柄即顶层窗口（勿用 GetParent）
    _apply_round_rect(hwnd, TOAST_W, TOAST_H, 14)  # 圆角半径与 Canvas 卡片一致
    _apply_acrylic(hwnd, ACCENT)
    win.attributes("-alpha", 0.9)

    # 淡入（渐渐显示）
    def fade_in(alpha=0.0):
        if alpha >= 0.85:
            win.attributes("-alpha", 0.9)
            return
        win.attributes("-alpha", max(alpha, 0.1))
        win.after(25, lambda: fade_in(alpha + 0.15))
    fade_in()

    # 从底部滑入到位
    target_y = sh - TOAST_H - TOAST_BOTTOM
    def slide_in(y):
        if y <= target_y:
            win.geometry(f"{TOAST_W}x{TOAST_H}+{x}+{target_y}")
            return
        win.geometry(f"{TOAST_W}x{TOAST_H}+{x}+{y}")
        win.after(12, lambda: slide_in(max(target_y, y - 12)))
    slide_in(sh)

    # 倒计时归零 → 淡出 → 销毁重排
    def fade_out():
        alpha = float(win.attributes("-alpha"))
        if alpha <= 0.05:
            _close_toast(win, quit_on_close)
            return
        win.attributes("-alpha", alpha - 0.08)
        win.after(30, fade_out)

    def tick():
        remaining[0] -= 1
        if remaining[0] <= 0:
            fade_out()
            return
        canvas.itemconfig(count_lbl, text=f"{remaining[0]}s")
        win.after(1000, tick)

    win.bind("<Button-1>", lambda _e: _close_toast(win, quit_on_close))
    win.after(1000, tick)
    return win


def _show_toast_ui(title, message, hint, duration_ms):
    """主线程内弹出 toast（由 mainloop 驱动）。"""
    try:
        _build_toast_window(title, message, hint, duration_ms)
    except Exception as e:
        log.warning("toast 提示失败: %s", e)


def _toast(title, message, hint="", wait=False, duration_ms=3000):
    """统一 toast：① 标题行（含倒计时）② 内容行（标签 · 内容）③ 提示行。

    异步（默认）调度到主线程 mainloop；wait=True（重复启动退出前）由窗口自身
    mainloop 同步驱动。不使用任何手动事件泵。
    """
    if wait:
        try:
            win = _build_toast_window(title, message, hint, duration_ms, quit_on_close=True)
            win.mainloop()  # 窗口事件循环：驱动倒计时，关闭（quit）后返回
        except Exception as e:
            log.warning("toast 提示失败: %s", e)
        return
    try:
        _tk_root.after(0, lambda: _show_toast_ui(title, message, hint, duration_ms))
    except Exception as e:
        log.warning("toast 调度失败: %s", e)


def _single_instance_lock():
    """创建互斥体防止重复启动。返回 True=唯一实例，False=已有实例在运行。"""
    global _mutex_handle
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _mutex_handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not _mutex_handle:
        return False
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS = 183


def on_press(key):
    try:
        char = key.char.lower()
    except AttributeError:
        char = key.name

    if char in pressed_set:
        return
    pressed_set.add(char)

    # F12 切换模式 / F11 多用途切换（本身不发声）
    if char == MODE_TOGGLE_KEY:
        toggle_mode()
        return
    if char == "f11":
        if MODE == MODE_MELODY:
            switch_melody()
        elif MODE == MODE_NOTE:
            switch_note_wave()
        else:
            switch_sound_profile()
        return

    action = CONTROL_KEYS.get(char)
    if action:
        handle_control(action)
        return

    if MODE == MODE_NOTE:
        note_engine.play(char)
    elif MODE == MODE_MELODY:
        melody_engine.play()
    else:
        engine.play(char)


def on_release(key):
    try:
        char = key.char.lower()
    except AttributeError:
        char = key.name
    if char in pressed_set:
        pressed_set.remove(char)
    # 幽灵模式：无退出方式，on_release 永远不返回 False


if __name__ == "__main__":
    if not _single_instance_lock():
        _toast("KeyTone 键响匣", "状态 已在运行，请勿重复启动", "任务管理器结束进程", wait=True)
        log.warning("检测到已有实例，本次启动退出")
        sys.exit(0)
    _toast("KeyTone 键响匣", "状态 已启动，后台静默运行中", "F12/F11 切换  F9/F10 音量")
    log.info("KeyTone 幽灵版启动")
    try:
        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.start()
        _tk_root.mainloop()  # 主线程事件循环：驱动 toast + 保持进程存活
    except Exception as e:
        log.error("监听器异常: %s", e)
