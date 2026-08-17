"""生成 KeyTone 测试用基础音效（标准库 wave，无需额外依赖）"""
import math
import os
import struct
import wave

SAMPLE_RATE = 44100


def synth(duration, freqs, decay=12.0, volume=0.5):
    """合成一段带指数衰减包络的正弦叠加波，返回 16bit PCM 采样列表"""
    n = int(SAMPLE_RATE * duration)
    frames = []
    for i in range(n):
        t = i / SAMPLE_RATE
        env = math.exp(-decay * t)  # 衰减包络，避免爆音
        v = sum(amp * math.sin(2 * math.pi * f * t) for f, amp in freqs) / len(freqs)
        frames.append(int(32767 * volume * env * v))
    return frames


def write_wav(path, frames):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(b"".join(struct.pack("<h", f) for f in frames))


# 每个键：时长 + 频率组合（基频 + 泛音，听感更饱满；低频双频做"噗"感）
SOUNDS = {
    "a":     dict(duration=0.18, freqs=[(440.00, 1.0), (880.00, 0.3)]),                # A4 中音"嘟"
    "b":     dict(duration=0.18, freqs=[(493.88, 1.0), (987.77, 0.3)]),                # B4 中音偏高"嘟"
    "space": dict(duration=0.12, freqs=[(130.81, 0.7), (261.63, 0.5)], decay=18.0),    # C3 低音短促"噗"
    "enter": dict(duration=0.22, freqs=[(220.00, 0.7), (330.00, 0.4)], decay=8.0),     # A3 稍长"咚"
    "esc":   dict(duration=0.15, freqs=[(880.00, 0.8), (1320.0, 0.3)], decay=16.0),    # A5 高音"滴"
    # 默认音：未映射按键也用这个，保证每个键都有声音
    "default": dict(duration=0.10, freqs=[(523.25, 1.0), (1046.5, 0.3)], decay=20.0),  # C5 中性短促"嗒"
}

if __name__ == "__main__":
    for name, params in SOUNDS.items():
        frames = synth(params["duration"], params["freqs"], decay=params.get("decay", 12.0))
        path = f"sounds/{name}.wav"
        write_wav(path, frames)
        print(f"generated {path} ({len(frames) / SAMPLE_RATE * 1000:.0f}ms)")
