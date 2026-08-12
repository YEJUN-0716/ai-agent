"""EP01 영상 조립. 장면별 오디오 + 화면 → ep01.mp4

    python make_video.py --check     녹음/화면이 뭐가 있고 뭐가 비었는지
    python make_video.py             있는 것만으로 조립

장면 길이는 **오디오 길이가 정한다.** 타임코드를 손으로 적지 않는다.
그래서 녹음을 통으로 하면 안 되고 장면별로 끊어야 한다.

대본은 `narration.md` 를 직접 읽는다. 여기에 다시 적지 않는다 — 두 군데
있으면 반드시 갈라지고, 갈라진 걸 아무도 모른다.

넣는 곳:
    audio/01.wav ... 11.wav      본인 녹음 (wav/mp3/m4a 아무거나)
    clips/03.mp4                 앱 화면 녹화 — 있으면 이미지 대신 쓴다
    charts/01_result.png ...     make_charts.py 산출물

없는 장면은 대본 첫 문장으로 자동 카드를 만들어 채운다. 그래서 녹음이
하나만 있어도 영상은 항상 만들어진다. 화면은 하나씩 갈아끼우면 된다.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
NARRATION = HERE / "narration.md"
AUDIO, CLIPS, CHARTS = HERE / "audio", HERE / "clips", HERE / "charts"
BUILD, OUT = HERE / "build", HERE / "ep01.mp4"

W, H, FPS = 1920, 1080, 30
BG, FG, MUTED = "#0b0f14", "#e6edf3", "#7d8590"

# 장면 → 미리 만들어 둔 화면. 없는 번호는 자동 카드.
VISUALS = {1: "01_result.png", 2: "02_r_explained.png", 6: "03_fill_rule.png",
           8: "01_result.png"}

AUDIO_EXT = (".wav", ".mp3", ".m4a", ".flac", ".aac")


def ff(name):
    p = shutil.which(name)
    if p:
        return p
    for guess in Path.home().glob(f"AppData/Local/Microsoft/WinGet/Links/{name}.exe"):
        return str(guess)
    sys.exit(f"{name} 을 찾을 수 없습니다. winget install Gyan.FFmpeg 후 셸을 새로 여세요.")


def scenes():
    """narration.md 본문을 빈 줄 두 개 기준으로 장면 분할."""
    body = NARRATION.read_text(encoding="utf-8").split("\n---\n", 1)[-1]
    blocks = [b.strip() for b in body.split("\n\n\n") if b.strip()]
    return [{"n": i, "text": b} for i, b in enumerate(blocks, 1)]


def find(folder, n, exts):
    for f in sorted(folder.glob(f"{n:02d}*")) if folder.exists() else []:
        if f.suffix.lower() in exts:
            return f
    return None


def duration(path):
    out = subprocess.run(
        [ff("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return float(json.loads(out.stdout)["format"]["duration"])


def card(scene, dest):
    """화면이 없는 장면용 자동 카드 — 대본 첫 문장을 크게."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import textwrap

    line = scene["text"].split("\n")[0].strip()
    fig, ax = plt.subplots(figsize=(W / 100, H / 100), dpi=100)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.axis("off")
    ax.text(0.5, 0.5, "\n".join(textwrap.wrap(line, 22)), color=FG,
            fontsize=54, weight="bold", ha="center", va="center",
            linespacing=1.5, family=["Malgun Gothic", "DejaVu Sans"])
    ax.text(0.5, 0.06, f"{scene['n']:02d}", color=MUTED, fontsize=24, ha="center")
    fig.savefig(dest, facecolor=BG)
    plt.close(fig)


def segment(scene, audio, visual, dest):
    """이미지(또는 클립) + 오디오 한 장면. 길이는 오디오가 정한다."""
    secs = duration(audio)
    is_clip = visual.suffix.lower() == ".mp4"
    vin = ["-stream_loop", "-1", "-i", str(visual)] if is_clip else \
          ["-loop", "1", "-i", str(visual)]
    scale = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
             f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={BG},setsar=1,fps={FPS}")
    subprocess.run(
        [ff("ffmpeg"), "-y", "-loglevel", "error", *vin, "-i", str(audio),
         "-t", f"{secs:.3f}", "-vf", scale,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "18",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
         "-map", "0:v:0", "-map", "1:a:0", "-shortest", str(dest)], check=True)
    return secs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="상태만 보고 끝낸다")
    args = ap.parse_args()

    sc = scenes()
    rows, ready = [], []
    for s in sc:
        a = find(AUDIO, s["n"], AUDIO_EXT)
        v = find(CLIPS, s["n"], (".mp4",)) or \
            (CHARTS / VISUALS[s["n"]] if s["n"] in VISUALS and
             (CHARTS / VISUALS[s["n"]]).exists() else None)
        rows.append((s["n"], a, v, s["text"].split("\n")[0][:38]))
        if a:
            ready.append((s, a, v))

    print(f"장면 {len(sc)}개 · 녹음 {len(ready)}개\n")
    for n, a, v, head in rows:
        print(f"  {n:02d} [{'♪' if a else ' '}] [{'▣' if v else '·'}]  {head}")
    print("\n  ♪ 녹음 있음   ▣ 전용 화면 있음(· 는 자동 카드)")

    if args.check:
        return 0
    if not ready:
        print(f"\n녹음이 하나도 없습니다. {AUDIO} 에 01.wav 부터 넣으세요.")
        return 1

    BUILD.mkdir(exist_ok=True)
    parts, total = [], 0.0
    for s, a, v in ready:
        if v is None:
            v = BUILD / f"card{s['n']:02d}.png"
            card(s, v)
        dest = BUILD / f"seg{s['n']:02d}.mp4"
        total += segment(s, a, v, dest)
        parts.append(dest)
        print(f"  장면 {s['n']:02d} 완료")

    listing = BUILD / "concat.txt"
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    subprocess.run([ff("ffmpeg"), "-y", "-loglevel", "error", "-f", "concat",
                    "-safe", "0", "-i", str(listing), "-c", "copy", str(OUT)], check=True)
    print(f"\n완성: {OUT}  ({int(total // 60)}분 {int(total % 60)}초)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
