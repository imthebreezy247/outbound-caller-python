"""
Generates a placeholder 30-second mono 48kHz call-center background WAV.
For production, replace assets/call_center_bg.wav with a real recording.
Good sources (free, CC0): freesound.org search "call center ambience" or
"office background chatter". Convert to mono 48kHz WAV:

  ffmpeg -i input.mp3 -ac 1 -ar 48000 assets/call_center_bg.wav

Run:  python assets/generate_ambience.py
"""
import math
import os
import random
import struct
import wave

OUT = os.path.join(os.path.dirname(__file__), "call_center_bg.wav")
SR = 48000
DUR = 30


def main() -> None:
    n = SR * DUR
    with wave.open(OUT, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        last = [0.0, 0.0, 0.0]
        for i in range(n):
            x = random.uniform(-1, 1)
            last = [last[1], last[2], x]
            v = sum(last) / 3 * 0.15  # low-passed noise
            v += 0.04 * math.sin(2 * math.pi * i * 0.7 / SR)  # slow breath wave
            v = max(-1.0, min(1.0, v))
            w.writeframes(struct.pack("<h", int(v * 32767)))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
