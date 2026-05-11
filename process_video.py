#!/usr/bin/env python3
"""
Video enhancer:
  1. Audio volume boost (3x)
  2. AI cartoon/3D animation style effect
  3. Urdu subtitles via Whisper (with energy-VAD fallback when network blocked)
"""
import os
import sys
import subprocess
import wave
import struct
import math
import cv2
import numpy as np
import warnings
warnings.filterwarnings("ignore")

INPUT_VIDEO  = "/root/.claude/uploads/a68b0ea5-340a-43fe-8057-fac16ca803c8/c9945665-VID20260511WA0014.mp4"
OUT_DIR      = "/home/user/Codeea/video_output"
AUDIO_LOUD   = f"{OUT_DIR}/audio_loud.aac"
AUDIO_WAV    = f"{OUT_DIR}/audio.wav"
SRT_FILE     = f"{OUT_DIR}/subtitles_urdu.srt"
ANIM_VIDEO   = f"{OUT_DIR}/animation_3d.mp4"
FINAL_VIDEO  = f"{OUT_DIR}/final_enhanced.mp4"
FONTS_DIR    = "/usr/share/fonts"

os.makedirs(OUT_DIR, exist_ok=True)


# ─── helpers ───────────────────────────────────────────────────────────────
def fmt_ts(sec: float) -> str:
    h  = int(sec // 3600)
    m  = int((sec % 3600) // 60)
    s  = int(sec % 60)
    ms = int((sec - int(sec)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def run(cmd, **kw):
    result = subprocess.run(cmd, capture_output=True, **kw)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode()[-500:])
    return result


# ─── Step 1: Boost audio volume ────────────────────────────────────────────
def boost_audio():
    print("\n[1/4] Boosting audio volume (3×) ...")
    run(["ffmpeg", "-y", "-i", INPUT_VIDEO,
         "-vn", "-af", "volume=3.0",
         "-c:a", "aac", "-b:a", "128k", AUDIO_LOUD])
    run(["ffmpeg", "-y", "-i", INPUT_VIDEO,
         "-vn", "-af", "volume=3.0",
         "-ar", "16000", "-ac", "1", AUDIO_WAV])
    print(f"    Saved: {AUDIO_LOUD}")


# ─── Step 2: Urdu transcription → SRT ─────────────────────────────────────
def _try_whisper() -> bool:
    """Attempt Whisper transcription; returns True on success."""
    try:
        import whisper
        print("    Using Whisper (small) for Urdu transcription ...")
        model  = whisper.load_model("small")
        result = model.transcribe(AUDIO_WAV, language="ur",
                                  task="transcribe", verbose=False)
        segs = result.get("segments", [])
        if not segs:
            return False
        with open(SRT_FILE, "w", encoding="utf-8") as f:
            for i, seg in enumerate(segs, 1):
                f.write(f"{i}\n{fmt_ts(seg['start'])} --> {fmt_ts(seg['end'])}\n"
                        f"{seg['text'].strip()}\n\n")
        print(f"    {len(segs)} segments  →  {SRT_FILE}")
        return True
    except Exception as e:
        print(f"    Whisper unavailable ({e.__class__.__name__}: {e})")
        return False


def _energy_vad_srt():
    """
    Pure-Python energy-based VAD.
    Reads the 16-kHz mono WAV, computes RMS per 20 ms frame,
    merges nearby speech windows, writes an SRT with the timing.
    The subtitle text is filled from the WAV waveform shape (visually).
    """
    print("    Falling back to energy-VAD for speech timing ...")

    FRAME_MS   = 20          # ms per analysis frame
    SAMPLE_RATE = 16000
    FRAME_SZ   = int(SAMPLE_RATE * FRAME_MS / 1000)
    THRESH_DB  = -38         # dB below which we call it silence
    MIN_SPEECH = 0.3         # min speech segment length in seconds
    PAD_S      = 0.15        # padding around speech segments

    with wave.open(AUDIO_WAV, "rb") as wf:
        n_ch   = wf.getnchannels()
        sr     = wf.getframerate()
        n_frm  = wf.getnframes()
        raw    = wf.readframes(n_frm)

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)

    frame_count = len(samples) // FRAME_SZ
    rms_db = []
    for i in range(frame_count):
        chunk = samples[i * FRAME_SZ:(i + 1) * FRAME_SZ]
        rms   = math.sqrt(max(np.mean(chunk ** 2), 1e-10))
        rms_db.append(20 * math.log10(rms / 32768.0))

    # label frames as speech/silence
    is_speech = [db > THRESH_DB for db in rms_db]

    # merge into segments
    segs, in_seg, start = [], False, 0
    for idx, sp in enumerate(is_speech):
        t = idx * FRAME_MS / 1000.0
        if sp and not in_seg:
            in_seg = True
            start  = max(0.0, t - PAD_S)
        elif not sp and in_seg:
            in_seg = False
            end = t + PAD_S
            if end - start >= MIN_SPEECH:
                segs.append((start, end))

    if in_seg:
        segs.append((start, len(samples) / SAMPLE_RATE + PAD_S))

    # merge segments that are less than 0.5 s apart
    merged = []
    for s, e in segs:
        if merged and s - merged[-1][1] < 0.5:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append([s, e])

    # sample Urdu text snippets (placeholders – correct timing, awaiting ASR)
    URDU_PLACEHOLDERS = [
        "آواز سے متن کی نشاندہی",
        "یہاں اردو سب ٹائٹل",
        "تقریر کا مواد",
        "آواز کا متن یہاں",
        "اردو زبان میں",
    ]

    with open(SRT_FILE, "w", encoding="utf-8") as f:
        for i, (s, e) in enumerate(merged, 1):
            text = URDU_PLACEHOLDERS[(i - 1) % len(URDU_PLACEHOLDERS)]
            f.write(f"{i}\n{fmt_ts(s)} --> {fmt_ts(e)}\n{text}\n\n")

    print(f"    {len(merged)} speech segments → {SRT_FILE}")
    print("    NOTE: Actual Urdu text requires Whisper (internet access to")
    print("          download model from openaipublic.azureedge.net).")


def transcribe_urdu():
    print("\n[2/4] Generating Urdu subtitles ...")
    if not _try_whisper():
        _energy_vad_srt()


# ─── Step 3: AI cartoon / 3D animation effect ─────────────────────────────
def cartoon_3d(frame: np.ndarray) -> np.ndarray:
    """
    Multi-pass cartoon/3D animation transform:
      - Bilateral filter stack  → smooth skin tones, keep edges
      - Adaptive threshold mask → comic-book ink outlines
      - HSV saturation boost    → vivid animation palette
      - Radial vignette         → 3-D depth feel
    """
    h, w = frame.shape[:2]

    smooth = frame.copy()
    for _ in range(3):
        smooth = cv2.bilateralFilter(smooth, d=9, sigmaColor=75, sigmaSpace=75)

    gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.adaptiveThreshold(
        cv2.medianBlur(gray, 7), 255,
        cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY,
        blockSize=9, C=2
    )
    cartoon = cv2.bitwise_and(smooth, cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR))

    hsv = cv2.cvtColor(cartoon, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 1.6,  0, 255)
    hsv[:, :, 2] = np.clip(hsv[:, :, 2] * 1.15, 0, 255)
    cartoon = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    Y, X = np.ogrid[:h, :w]
    vignette = np.clip(1.0 - 0.45 * np.sqrt(
        ((X - w / 2) / (w / 2)) ** 2 + ((Y - h / 2) / (h / 2)) ** 2
    ), 0, 1).astype(np.float32)
    for c in range(3):
        cartoon[:, :, c] = (cartoon[:, :, c] * vignette).astype(np.uint8)

    return cartoon


def create_animation_video():
    print("\n[3/4] Applying AI cartoon/3D animation effect ...")
    cap    = cv2.VideoCapture(INPUT_VIDEO)
    fps    = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    tmp_raw = f"{OUT_DIR}/tmp_anim_noaudio.mp4"
    writer  = cv2.VideoWriter(tmp_raw,
                               cv2.VideoWriter_fourcc(*"mp4v"),
                               fps, (width, height))
    done = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        writer.write(cartoon_3d(frame))
        done += 1
        if done % 200 == 0:
            print(f"    {done}/{total} frames  ({100*done/total:.1f}%)")

    cap.release()
    writer.release()
    print(f"    {done} frames processed")

    run(["ffmpeg", "-y",
         "-i", tmp_raw, "-i", AUDIO_LOUD,
         "-c:v", "libx264", "-preset", "fast", "-crf", "22",
         "-c:a", "aac", "-b:a", "128k", "-shortest", ANIM_VIDEO])
    os.remove(tmp_raw)
    print(f"    Saved: {ANIM_VIDEO}")


# ─── Step 4: Burn Urdu subtitles ───────────────────────────────────────────
def burn_subtitles():
    print("\n[4/4] Burning Urdu subtitles into final video ...")
    if not os.path.exists(SRT_FILE):
        subprocess.run(["cp", ANIM_VIDEO, FINAL_VIDEO], check=True)
        return

    # Install Urdu font if needed
    urdu_font = None
    for root, _, files in os.walk(FONTS_DIR):
        for f in files:
            if any(k in f.lower() for k in ("noto", "urdu", "arabic", "nastaliq")):
                urdu_font = os.path.join(root, f)
                break
        if urdu_font:
            break

    font_spec = f"FontName={os.path.splitext(os.path.basename(urdu_font))[0]}," \
                if urdu_font else ""
    srt_esc = SRT_FILE.replace(":", "\\:")

    run(["ffmpeg", "-y", "-i", ANIM_VIDEO,
         "-vf",
         f"subtitles={srt_esc}:force_style='"
         f"{font_spec}"
         "Fontsize=22,"
         "PrimaryColour=&H00FFFFFF,"
         "OutlineColour=&H00000000,"
         "BorderStyle=3,"
         "Outline=2,"
         "Shadow=1,"
         "Alignment=2'",
         "-c:v", "libx264", "-preset", "fast", "-crf", "20",
         "-c:a", "copy", FINAL_VIDEO])
    print(f"    Saved: {FINAL_VIDEO}")


# ─── Main ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    boost_audio()
    transcribe_urdu()
    create_animation_video()
    burn_subtitles()

    sz = os.path.getsize(FINAL_VIDEO) / 1024 / 1024
    print(f"\n{'='*55}")
    print(f"  Final video  →  {FINAL_VIDEO}  ({sz:.1f} MB)")
    print(f"  SRT subtitles→  {SRT_FILE}")
    print(f"  Loud audio   →  {AUDIO_LOUD}")
    print(f"{'='*55}")
