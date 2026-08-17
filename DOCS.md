# KeyTone 键响匣 · 详细设计文档（幽灵版）

| 项目 | 内容 |
|---|---|
| 版本 | 0.2（幽灵版） |
| 日期 | 2026-08-17 |
| 形态 | 全局键盘按键音效程序（无窗口、无托盘、无退出入口） |
| 技术栈 | Python 3.13 / pynput / pygame（SDL 2.28） |
| 代码位置 | `D:/jetBrainsPycharm/KeyTone/` |

---

## 1. 项目概述

### 1.1 功能
- 全局监听键盘，**每个按键绑定独立 wav 音效**，按下触发一次
- 长按不重复狂响（`pressed_set` 去重）
- 未映射的按键播放**默认音效**，保证每个键都有声音
- 运行中**静默调节**：`=`/`-` 调音量（0~1），`]`/`[` 调音调（0.5~2.0）
- **幽灵模式**：开启后找不到关闭的地方——无窗口、无控制台、无托盘图标、无任何提示、无退出快捷键
- 启动弹自定义轻量 toast「已启动」；重复启动弹 toast「已在运行」并自动退出（tkinter 进程内绘制，几十毫秒，无系统弹窗）
- **单实例保护**：命名互斥体确保同一用户会话内只有一个实例运行
- **音符弹琴模式**：F12 切换；任意按键按下都发一个合成电子琴音（无需 wav，lazy 合成 + 缓存）
- **预制旋律模式**：内置乐谱（《小星星》），不管按哪个键都按乐谱顺序推进播放，到尾循环
- F12 在三种模式间循环切换（toast 提示）

### 1.2 设计原则
1. **单文件主程序**：`main.py` 自包含全部逻辑，方便拷贝部署
2. **可扩展接口**：核心逻辑封装为 `SoundEngine` 类，后续接 GUI / 外部控制只需调用 `set_volume` / `set_pitch` / `play`
3. **失败不崩溃**：音效加载失败只记日志并静默跳过
4. **排障有出口**：幽灵模式无界面提示，统一写 `keytone.log` 文件日志

---

## 2. 快速开始

### 2.1 安装依赖
```bash
pip install pynput pygame
```

### 2.2 准备音效
在项目目录下创建 `sounds/` 文件夹，放入 wav 文件。可用内置生成器快速生成测试音：
```bash
python generate_sounds.py
```
生成：`a.wav`、`b.wav`、`space.wav`、`enter.wav`、`esc.wav`、`default.wav`

### 2.3 启动（幽灵模式）
双击 `start_ghost.vbs`（无窗口），或命令行运行：
```bash
pythonw main.py
```
启动成功弹自定义轻量 toast「已启动，后台静默运行中」；若已有实例在运行，弹 toast「已在运行，请勿重复启动」并自动退出。toast 由 tkinter 进程内绘制（无系统弹窗，几十毫秒响应，右下角自动消失）。
> 无需管理员权限：pynput 低级键盘钩子（WH_KEYBOARD_LL）普通权限即可全局监听；唯一限制是监听不到"以管理员身份运行"的窗口内的按键（UAC 隔离）。

### 2.4 停止
没有任何退出方式，只能任务管理器结束进程：
`Ctrl+Shift+Esc` → 进程列表找到 `pythonw.exe`（或 `python.exe`）→ 右键结束任务。

> ⚠️ 测试建议：先用 `python main.py` 前台运行确认效果，确认无误后再用幽灵模式。

---

## 3. 文件结构

| 文件 | 作用 | 可否删除 |
|---|---|---|
| `main.py` | 主程序（三种模式/打断播放/单实例/toast/日志） | 否 |
| `start_ghost.vbs` | 双击无窗口启动脚本（源码版） | 否 |
| `generate_sounds.py` | 测试音效生成器（纯标准库） | 可选 |
| `download_douyin.py` | 抖音视频下载工具（Cookie 从 `douyin_cookies.txt` 读取） | 可选 |
| `douyin_cookies.txt` | 抖音登录 Cookie（敏感，**不入库**） | 用完可删 |
| `KeyTone.spec` | PyInstaller 打包配置 | 否 |
| `dist/KeyTone.exe` | 打包产物（双击即用，**不入库**） | 可随时重新打包 |
| `README.md` | 简明使用说明 | 可选 |
| `DOCS.md` | 本文档（详细设计） | 可选 |
| `sounds/*.wav` | 按键音效（enter.wav 为「牛来叫妈妈」前 5 秒） | 否（缺了就没声音） |
| `keytone.log` | 运行日志（自动生成） | 可随时删除 |

---

## 4. 架构设计

### 4.1 整体流程

```
main.py 启动
  │
  ├─ 初始化音频 pygame.mixer.init(44100Hz, 16bit, 单声道)
  ├─ 定义 KEY_SOUND_MAP / DEFAULT_SOUND
  ├─ 实例化 SoundEngine（预加载全部 wav，含默认音）
  ├─ 启动 pynput.keyboard.Listener（全局监听，守护线程）
  │     ├─ on_press(key)   → 防重复判断 → 控制键调节 or 播放音效
  │     └─ on_release(key) → 从 pressed_set 移除
  └─ listener.join() 永久阻塞（无退出路径）
```

### 4.2 SoundEngine 类（核心引擎）

```python
class SoundEngine:
    def __init__(self, sound_map, default_sound)  # 预加载全部音效
    @property volume / pitch                       # 当前音量 / 音调
    def set_volume(vol)                            # 0~1 范围 clamp
    def set_pitch(mult)                            # 0.5~2.0 范围 clamp
    def play(name)                                 # 播放；未映射回退默认音
    # 内部：_load / _resample / _get_sound
```

**内部数据结构**

| 成员 | 类型 | 用途 |
|---|---|---|
| `_sounds` | `dict[str, pygame.mixer.Sound]` | 原始音效（名称 → Sound） |
| `_raw` | `dict[str, array('h')]` | 原始采样（用于变调重采样） |
| `_cache` | `dict[(名称, 音调), Sound]` | 变调结果缓存，避免重复重采样 |
| `_default` | `str` | 默认音内部名 `"__default__"` |

### 4.2.1 NoteEngine（音符引擎）

| 设计点 | 说明 |
|---|---|
| lazy 合成 | 首次按下某音才合成（0.4s 电子琴音：正弦 + 2/3 次泛音 + 指数衰减），结果按 MIDI 号缓存，之后零延迟 |
| 音高映射 | `NOTE_SEQUENCE` 顺序表 = 低音特殊键 + 主键盘 4 行（行低→高展开）+ 高音 F 键，序号即半音偏移，基准 A2（MIDI 45） |
| 音量跟随 | 播放时 `set_volume(engine.volume * 0.7)`，与按键音效模式共享全局音量调节 |
| 静默规则 | 不在布局内的按键（如 F12 切换键）不发声 |

### 4.2.2 MelodyEngine（预制旋律引擎）

| 设计点 | 说明 |
|---|---|
| 乐谱 | `MELODY` 简谱字符串（1-7 为 do-si，`'` 高八度、`.` 低八度、`0` 休止，空格仅分组），默认《小星星》 |
| 播放规则 | 不管按哪个键，都播放 `_notes[_pos]` 并推进游标；到尾 `(pos+1) % len` 循环 |
| 简谱转音高 | `_solfa_to_midi`：简谱 → MIDI（C 大调 do=C4），休止符返回 None（无声推进） |
| 音色/缓存 | 复用 `NoteEngine.play_midi`（共享合成缓存），音量跟随全局 |

### 4.2.3 模式切换

`MODE` 全局变量（0=按键音效 / 1=音符弹琴 / 2=预制旋律），F12 触发 `toggle_mode()`：`(MODE+1) % 3` 循环切换并弹 toast 提示，不发声。

**预加载设计**：初始化时一次性把所有 wav 读入内存（`Sound` + 原始采样），运行时按键不再读文件，播放零延迟。

**变调缓存设计**：同一音调倍率下每个音效只重采样一次，缓存以 `(名称, 音调)` 为键；调节音调后首次播放该音效时生成，之后直接命中缓存。

### 4.3 回调机制（pynput）

- `on_press(key)`：归一化按键字符 → `pressed_set` 去重 → 命中控制键则调节，否则 `engine.play(char)`
- `on_release(key)`：从 `pressed_set` 移除；**永不返回 False**（无退出路径）

按键归一化规则：
```python
try:
    char = key.char.lower()   # 字母键：'a' 'b' ...
except AttributeError:
    char = key.name           # 特殊键：'space' 'enter' 'esc' ...
```

### 4.4 幽灵模式设计

| 需求 | 实现方式 |
|---|---|
| 无窗口 | `pythonw.exe` 运行（vbs 脚本启动，无控制台） |
| 无托盘 | 不引入任何托盘库（pystray 等） |
| 无界面提示 | 删除全部 `print`，调节静默生效；仅启动/重复启动/切换时弹一次轻量 toast |
| 启动反馈 | 自定义 tkinter toast：无边框置顶、右下角、自动消失（进程内绘制，几十毫秒） |
| 单实例保护 | 命名互斥体 `Local\KeyTone_SingleInstance`；重复启动弹 toast 提醒并自动退出 |
| 无退出方式 | 删除 ESC 退出逻辑，`on_release` 不返回 False，`join()` 永久阻塞 |
| 排障出口 | `logging` 写入 `keytone.log`（启动/异常/加载失败） |
| 启动容错 | 音效缺失只记日志，不崩溃；初始化失败写日志后退出 |

---

## 5. 关键机制详解

### 5.1 长按防重复（pressed_set）

```python
pressed_set = set()          # 记录"已按下未抬起"的按键

def on_press(key):
    ...
    if char in pressed_set:  # 长按期间系统重复回调 → 直接忽略
        return
    pressed_set.add(char)    # 首次按下才放行
    engine.play(char)
```

- 原理：pynput 在长按时会重复触发 `on_press`，用集合记录按下状态，同一按键在抬起前只响应一次
- `on_release` 中从集合移除，保证下一次按下能再次触发

### 5.2 音量调节（set_volume）

- 范围 `0.0 ~ 1.0`，越界自动 clamp
- 实现：`pygame.mixer.Sound.set_volume()`，播放前每次都应用当前音量（保证缓存音效也生效）
- 调节键：`=` 加、`-` 减，步进 `ADJUST_STEP = 0.05`

### 5.3 音调调节（set_pitch + 线性插值重采样）

`pygame` 原生不支持变调，采用**采样重采样**实现：

```python
def _resample(self, samples, rate):
    out_len = int(len(samples) / rate)   # rate>1 输出变短 → 音调变高
    ...
    for _ in range(out_len):
        i0 = int(pos)
        if i0 >= last:                   # 降调时输出长于输入，末尾保持
            i0, i1, frac = last, last, 0.0
        else:
            i1, frac = i0 + 1, pos - i0
        out.append(int(samples[i0]*(1-frac) + samples[i1]*frac))
        pos += step
```

| rate | 效果 | 输出长度 |
|---|---|---|
| 1.0 | 原音 | 不变 |
| 1.5 | 音调变高（时长变短，180ms→120ms） | 2/3 |
| 0.7 | 音调变低（时长变长，120ms→171ms） | 约 1.43 倍 |
| 0.5 | 更低（180ms→360ms） | 2 倍 |

- 本质是"变速变调"近似（时长随音调变化），对短促按键音听感自然
- **越界修复**：降调时输出长度超过输入采样数，原实现索引越界崩溃；修复为末尾保持最后一个采样（衰减包络尾部近静音，听感无影响）
- 缓存：`(名称, 音调)` 为键缓存变调结果，避免每个按键重复计算

### 5.4 默认音回退

```python
def play(self, name):
    if name not in self._sounds:
        name = self._default      # 未映射键 → 默认音
    if name not in self._sounds:
        return                    # 默认音也缺失 → 静默放弃
    snd = self._get_sound(name)
    snd.set_volume(self._volume)
    snd.play()
```

层级：已映射音效 → 默认音（`default.wav`）→ 静默。保证任何按键都不会崩溃。

### 5.5 音频初始化参数

```python
pygame.mixer.init(frequency=44100, size=-16, channels=1)
```

- 显式指定**单声道 16bit**：`get_raw()` 返回的原始采样格式确定，变调重采样按 1 采样 = 2 字节处理
- 若用 pygame 默认（双声道），重采样逻辑需按帧处理，会复杂得多

### 5.6 打断式播放

```python
pygame.mixer.set_num_channels(1)   # 单通道限制

def _play_interrupt(snd):
    pygame.mixer.stop()            # 先停掉所有正在播的音
    snd.play()                     # 再播新音
```

- **问题背景**：pygame 的 `Sound.play()` 在通道全忙时**不抢占**（直接放弃播放），快速连按会导致旧音继续、新音丢失、叠加混乱
- **解法**：`set_num_channels(1)` 限制并发 + 每次播放前 `pygame.mixer.stop()` 显式打断——新按键音必定替换旧音
- 应用点：`SoundEngine.play`（按键音效）、`NoteEngine.play_midi`（音符/旋律）统一走 `_play_interrupt`

---

## 6. 使用指南

### 6.1 自定义按键映射

编辑 `main.py` 顶部 `KEY_SOUND_MAP`：

```python
KEY_SOUND_MAP = {
    "a": "sounds/a.wav",          # 字母键用字符
    "space": "sounds/space.wav",  # 特殊键用 key.name
    "enter": "sounds/enter.wav",
    "esc": "sounds/esc.wav",
}
```

- 字母/数字/符号键：`key.char`（小写）
- 功能键：`key.name`，如 `space` / `enter` / `esc` / `shift` / `ctrl` / `tab` / `caps_lock`
- 音效文件必须真实存在，否则该键静默无声音（日志有 warning）

### 6.2 运行中静默调节（幽灵模式下同样生效）

| 按键 | 作用 | 范围 | 步进 |
|---|---|---|---|
| `=` | 音量 +0.05 | 0 ~ 1.0 | 0.05 |
| `-` | 音量 -0.05 | 0 ~ 1.0 | 0.05 |
| `]` | 音调 +0.05 | 0.5 ~ 2.0 | 0.05 |
| `[` | 音调 -0.05 | 0.5 ~ 2.0 | 0.05 |

> 控制键本身不发声，且同样受长按防重复保护（长按不会连续调节）。

### 6.4 音符弹琴模式

| 项目 | 说明 |
|---|---|
| 切换 | F12（按键音效 ↔ 音符弹琴），切换弹 toast 提示 |
| 发声规则 | 任意按键都发声：主键盘按行映射音高（数字行最高→ZXCV 行最低，行内左低右高），特殊键低音区，F1~F11 高音区 |
| 音色 | 合成电子琴音（正弦+泛音+指数衰减，0.4s），无 wav 依赖，lazy 合成 + 缓存 |
| 控制键 | `=`/`-` 音量、`]`/`[` 音调在两种模式下均生效 |
| 静默按键 | F12 及不在布局内的键不发声 |

### 6.5 预制旋律模式

| 项目 | 说明 |
|---|---|
| 切换 | F12 循环：按键音效 → 音符弹琴 → 预制旋律 |
| 播放规则 | 不管按哪个键，都按 `MELODY` 乐谱顺序播放下一个音，到尾循环（音乐盒效果） |
| 自定义乐谱 | 改 `main.py` 的 `MELODY` 常量：简谱 1-7，`'` 高八度（如 `5'`）、`.` 低八度（如 `5.`）、`0` 休止、空格分组 |
| 音色 | 与音符模式共享合成电子琴音与缓存 |
| 控制键 | `=`/`-` 音量、`]`/`[` 音调调节同样生效 |

### 6.6 配置文件（config.json）

启动时自动生成于程序同目录（`APP_DIR/config.json`），缺失/损坏时回退默认值并重建：

| 字段 | 说明 |
|---|---|
| `sound_map` | 按键 → 音效路径；相对路径经 `resource_path` 解析（打包资源），绝对路径直接使用（自定义外部音效） |
| `default_sound` | 未映射按键默认音效 |
| `melody` | 预制旋律乐谱 |
| `volume` / `pitch` | 音量/音调；启动时应用到引擎，运行时调节经 `handle_control` 自动写回 |

实现：`load_config()`（深拷贝默认值 + 合并用户字段）/ `save_config()`（indent=2 可读格式）；`CONFIG` 为模块级字典，引擎与旋律引擎均从其取值。

### 6.3 音效生成器参数（generate_sounds.py）

```python
SOUNDS = {
    "a":     dict(duration=0.18, freqs=[(440.00, 1.0), (880.00, 0.3)]),  # 基频+泛音
    "space": dict(duration=0.12, freqs=[(130.81, 0.7), (261.63, 0.5)], decay=18.0),
    ...
}
```

| 参数 | 含义 |
|---|---|
| `duration` | 时长（秒），0.10 ~ 0.22 |
| `freqs` | `[(频率Hz, 振幅权重)]` 列表，多频叠加更饱满；低频双频做"噗"感 |
| `decay` | 指数衰减速率（默认 12），越大越短促，避免爆音 |
| `volume` | 合成音量（默认 0.5） |

修改后重跑 `python generate_sounds.py` 即可重新生成全部 wav。

---

## 7. 故障排查

| 现象 | 可能原因 | 解决办法 |
|---|---|---|
| 没有任何声音 | 音效文件缺失 / 系统扬声器静音 | 检查 `sounds/` 与系统音量；看 `keytone.log` |
| 监听不到某窗口按键 | 该窗口以管理员权限运行 | 属正常限制（UAC 隔离），用普通权限窗口测试 |
| 某个键没声音 | 对应 wav 缺失或路径不对 | 检查 `sounds/` 文件名与 `KEY_SOUND_MAP`；看 `keytone.log` 的 warning |
| 程序静默退出 | 音频初始化失败 | 查 `keytone.log` 的 error 记录 |
| 音量/音调调节无效 | 按键被 IME 或其他程序占用 | 换用 `]`/`[` 或 `=`/`-` 测试；确认不是长按 |
| 找不到进程 | 任务管理器没显示 | 勾选"显示所有用户的进程"，找 `pythonw.exe` |
| 音调调高后刺耳 | 重采样本质是变速 | 音调范围限制 0.5~2.0，建议 0.8~1.5 使用 |

---

## 8. 注意事项

1. **权限**：无需管理员权限（pynput 低级钩子普通权限即可全局监听）；但监听不到"以管理员身份运行"的窗口内的按键（UAC 隔离）
2. **幽灵程序风险**：本程序无退出入口，只能任务管理器杀进程；请勿在无法管理的环境（如远程会话）启动
3. **隐私**：全局监听会捕获所有按键输入（仅用于触发音效，不记录内容、不联网）；请知晓后使用
4. **资源占用**：音效预加载 + 变调缓存，全部 wav 常驻内存；单个 200ms 44100Hz 单声道 wav 约 17KB，通常无感
5. **日志文件**：`keytone.log` 会持续追加，可随时删除；不会无限增长（无循环写入）

---

## 9. 打包 exe（已完成）

```bash
.venv/Scripts/python.exe -m PyInstaller --noconsole --onefile --name KeyTone --add-data "sounds;sounds" main.py
# 或复用已有配置：.venv/Scripts/python.exe -m PyInstaller KeyTone.spec
```

**打包适配要点**：
- `resource_path()`：PyInstaller onefile 运行时资源在 `_MEIPASS` 临时目录，用它解析 wav 路径（源码运行回退到项目目录）
- `APP_DIR`：日志写到 exe 所在目录（`sys.executable` 目录），避免写进临时解压区
- `--noconsole`：无控制台窗口；`--onefile`：单文件分发
- 产物 `dist/KeyTone.exe` 约 17.7MB，双击即用，无需 Python 环境

## 10. 后续规划

1. **tkinter 图形界面**：可视化编辑按键-音效映射，调节滑块直接调用 `SoundEngine` 接口
2. **json 配置持久化**：`KEY_SOUND_MAP`、音量、音调存入配置文件，重启不丢失

> 架构已为以上扩展预留接口：调节走 `SoundEngine.set_volume/set_pitch`，映射走配置文件即可，主循环无需改动。
