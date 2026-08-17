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
DEFAULT_CONFIG = {
    "sound_map": dict(KEY_SOUND_MAP),
    "default_sound": DEFAULT_SOUND,
    "melody": "1155665 4433221 5544332 5544332 1155665 4433221",
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

    def _load(self, name, path):
        full = path if os.path.isabs(path) else resource_path(path)
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

    def _synth(self, midi):
        """合成电子琴音：正弦 + 2/3 次泛音 + 指数衰减，0.4s。"""
        freq = 440.0 * (2.0 ** ((midi - 69) / 12.0))
        n = int(SAMPLE_RATE * 0.4)
        # 按 mixer 实际声道数生成交错数据（SDL 可能把 mono 请求改写为 stereo）
        _, _, channels = pygame.mixer.get_init()
        frames = []
        for i in range(n):
            t = i / SAMPLE_RATE
            env = math.exp(-8.0 * t)
            v = (math.sin(2.0 * math.pi * freq * t)
                 + 0.5 * math.sin(4.0 * math.pi * freq * t)
                 + 0.25 * math.sin(6.0 * math.pi * freq * t)) / 1.75
            s = int(32767 * env * v)
            frames.append(s)
            if channels == 2:
                frames.append(s)  # 复制为 L/R 对
        return pygame.mixer.Sound(buffer=array.array("h", frames).tobytes())

    def play_midi(self, midi):
        """按 MIDI 编号播放音符（共享缓存）；midi 为 None 时静默。"""
        if midi is None:
            return
        snd = self._cache.get(midi)
        if snd is None:
            snd = self._synth(midi)
            self._cache[midi] = snd
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
    """预制旋律引擎：不管按哪个键，都按乐谱顺序播放下一个音，到尾循环。"""

    def __init__(self, melody):
        self._notes = [c for c in melody if c != " "]
        self._pos = 0

    def play(self):
        """播放当前位置的音符并推进游标（休止符无声推进）。"""
        if not self._notes:
            return
        midi = _solfa_to_midi(self._notes[self._pos])
        self._pos = (self._pos + 1) % len(self._notes)
        note_engine.play_midi(midi)


# 音符引擎实例 + 当前模式
note_engine = NoteEngine()
melody_engine = MelodyEngine(CONFIG["melody"])
MODE = MODE_SOUND


def toggle_mode():
    """循环切换三种模式（F12），toast 提示。"""
    global MODE
    MODE = (MODE + 1) % 3
    _toast("KeyTone 键响匣", MODE_NAMES[MODE])
    log.info("模式切换: %s", MODE_NAMES[MODE])

def _play_interrupt(snd):
    """打断式播放：先停止所有正在播放的音，再播新音（新按键音打断旧音）。"""
    pygame.mixer.stop()
    snd.play()


# 控制键：= / - 调音量，] / [ 调音调（静默调节，按键本身不发声）
CONTROL_KEYS = {
    "=": "vol_up",
    "-": "vol_down",
    "]": "pitch_up",
    "[": "pitch_down",
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


def _toast(title, message, wait=False, duration_ms=1600):
    """自定义轻量 toast：tkinter 无边框置顶小窗，右下角显示，自动消失。

    进程内创建（无需 PowerShell 子进程，几十毫秒），响应快；
    wait=True 时等待显示完毕再返回（用于重复启动后退出前）。
    """
    def worker():
        try:
            root = tk.Tk()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.attributes("-alpha", 0.92)
            w, h = 280, 64
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
            root.geometry(f"{w}x{h}+{sw - w - 24}+{sh - h - 24}")
            frame = tk.Frame(root, bg="#1f1f1f")
            frame.pack(fill="both", expand=True)
            tk.Label(frame, text=title, bg="#1f1f1f", fg="#ffffff",
                     font=("Microsoft YaHei", 10, "bold")).pack(pady=(10, 0))
            tk.Label(frame, text=message, bg="#1f1f1f", fg="#cccccc",
                     font=("Microsoft YaHei", 9)).pack(pady=(2, 10))
            root.bind("<Button-1>", lambda _e: root.destroy())
            root.after(duration_ms, root.destroy)
            root.mainloop()
        except Exception as e:
            log.warning("toast 提示失败: %s", e)
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    if wait:
        t.join(timeout=duration_ms / 1000 + 0.3)


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

    # F12 切换 按键音效 ↔ 音符弹琴 模式（本身不发声）
    if char == MODE_TOGGLE_KEY:
        toggle_mode()
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
        _toast("KeyTone 键响匣", "已在运行，请勿重复启动", wait=True)
        log.warning("检测到已有实例，本次启动退出")
        sys.exit(0)
    _toast("KeyTone 键响匣", "已启动，后台静默运行中")
    log.info("KeyTone 幽灵版启动")
    try:
        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.start()
        listener.join()
    except Exception as e:
        log.error("监听器异常: %s", e)
