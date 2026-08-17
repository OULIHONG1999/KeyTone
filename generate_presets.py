"""生成预制音效库：12 半音 × 3 个八度 = 36 个电子琴音，写入 presets/

音色与 NoteEngine 合成一致（正弦 + 2/3 次泛音 + 指数衰减），
文件名如 C3.wav / Cs3.wav（# 用 s 代替，避免文件名/配置转义麻烦），
供 config.json 的 sound_map 配置使用。
"""
import math
import os
import struct
import wave

SAMPLE_RATE = 44100
NOTE_NAMES = ["C", "Cs", "D", "Ds", "E", "F", "Fs", "G", "Gs", "A", "As", "B"]


def synth_tone(freq, duration=0.3, decay=8.0):
    """电子琴音：正弦 + 2/3 次泛音 + 指数衰减。"""
    n = int(SAMPLE_RATE * duration)
    frames = []
    for i in range(n):
        t = i / SAMPLE_RATE
        env = math.exp(-decay * t)
        v = (math.sin(2.0 * math.pi * freq * t)
             + 0.5 * math.sin(4.0 * math.pi * freq * t)
             + 0.25 * math.sin(6.0 * math.pi * freq * t)) / 1.75
        frames.append(int(32767 * 0.5 * env * v))
    return frames


def write_wav(path, frames):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(b"".join(struct.pack("<h", f) for f in frames))


if __name__ == "__main__":
    count = 0
    for octave in (3, 4, 5):  # C3 ~ B5 三个八度
        for idx, name in enumerate(NOTE_NAMES):
            midi = 12 * (octave + 1) + idx
            freq = 440.0 * (2.0 ** ((midi - 69) / 12.0))
            write_wav(f"presets/{name}{octave}.wav", synth_tone(freq))
            count += 1
    print(f"生成 {count} 个预制音效到 presets/")
