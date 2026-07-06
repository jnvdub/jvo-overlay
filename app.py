from flask import Flask, request, jsonify, send_file
from PIL import Image, ImageDraw, ImageFont
import requests as req_lib
import io
import os
import uuid
import subprocess
import shutil
import math
import numpy as np

app = Flask(__name__)

TEMP_DIR = "/tmp/jvo_images"
os.makedirs(TEMP_DIR, exist_ok=True)

# Vendored fonts shipped in the repo (fonts/ dir next to this file)
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


def find_font(candidates):
    for path in candidates:
        if os.path.exists(path):
            return path
    return None

QUOTE_FONT_PATH = find_font([
    "/usr/share/fonts/truetype/dejavu/DejaVuSerifCondensed-BoldItalic.ttf",
    "/usr/share/fonts/dejavu/DejaVuSerifCondensed-BoldItalic.ttf",
])
REF_FONT_PATH = find_font([
    "/usr/share/fonts/truetype/dejavu/DejaVuSerifCondensed-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSerifCondensed-Bold.ttf",
])
FALLBACK_FONT_PATH = find_font([
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
])

GOLD = (255, 248, 232)
TEAL = (0, 201, 167)
CANVAS_SIZE = (1080, 1080)
VIDEO_W, VIDEO_H = 1080, 1920

def load_font(path, size):
    try:
        if path and os.path.exists(path):
            return ImageFont.truetype(path, size)
    except Exception:
        pass
    if FALLBACK_FONT_PATH and os.path.exists(FALLBACK_FONT_PATH):
        try:
            return ImageFont.truetype(FALLBACK_FONT_PATH, size)
        except Exception:
            pass
    return ImageFont.load_default()

def draw_text_with_shadow(draw, text, position, font, text_color, shadow_offset=3):
    x, y = position
    draw.text((x + shadow_offset, y + shadow_offset), text, font=font, fill=(0, 0, 0, 180))
    draw.text((x, y), text, font=font, fill=text_color)

def wrap_text(text, font, max_width, draw):
    words = text.split()
    lines, current_line = [], []
    for word in words:
        test_line = ' '.join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(' '.join(current_line))
            current_line = [word]
    if current_line:
        lines.append(' '.join(current_line))
    return lines

def compose_image(image_url, quote_text, reference):
    response = req_lib.get(image_url, timeout=30)
    response.raise_for_status()
    base_img = Image.open(io.BytesIO(response.content)).convert("RGBA")
    base_img = base_img.resize(CANVAS_SIZE, Image.LANCZOS)
    overlay = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    padding = 60
    max_text_width = CANVAS_SIZE[0] - (padding * 2)

    quote_font_size = 52
    quote_font = load_font(QUOTE_FONT_PATH, quote_font_size)
    while quote_font_size > 28:
        quote_font = load_font(QUOTE_FONT_PATH, quote_font_size)
        lines = wrap_text(f'"{quote_text}"', quote_font, max_text_width, draw)
        if len(lines) <= 4:
            break
        quote_font_size -= 4

    lines = wrap_text(f'"{quote_text}"', quote_font, max_text_width, draw)
    line_height = quote_font_size + 10
    ref_font = load_font(REF_FONT_PATH, 38)
    brand_font = load_font(FALLBACK_FONT_PATH, 24)

    quote_y = int(CANVAS_SIZE[1] * 0.52)
    panel_top = quote_y - 30
    panel_height = CANVAS_SIZE[1] - panel_top
    for y in range(panel_height):
        alpha = int(220 * (y / panel_height))
        draw.rectangle([(0, panel_top + y), (CANVAS_SIZE[0], panel_top + y + 1)], fill=(7, 4, 18, alpha))

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=quote_font)
        x = (CANVAS_SIZE[0] - (bbox[2] - bbox[0])) // 2
        draw_text_with_shadow(draw, line, (x, quote_y), quote_font, (*GOLD, 255))
        quote_y += line_height

    line_y = quote_y + 20
    draw.rectangle([(padding, line_y), (CANVAS_SIZE[0] - padding, line_y + 3)], fill=(*TEAL, 255))

    ref_text = f"— {reference}, KJV"
    bbox = draw.textbbox((0, 0), ref_text, font=ref_font)
    ref_x = (CANVAS_SIZE[0] - (bbox[2] - bbox[0])) // 2
    ref_y = line_y + 16
    draw_text_with_shadow(draw, ref_text, (ref_x, ref_y), ref_font, (*TEAL, 255))

    brand_text = "@only.jesusvibes"
    bbox = draw.textbbox((0, 0), brand_text, font=brand_font)
    brand_x = (CANVAS_SIZE[0] - (bbox[2] - bbox[0])) // 2
    brand_y = ref_y + 52
    draw_text_with_shadow(draw, brand_text, (brand_x, brand_y), brand_font, (255, 255, 255, 160))

    result = Image.alpha_composite(base_img, overlay).convert("RGB")
    return result


# ── LOOPING VIDEO OVERLAY (square 1080, Montserrat) ───────────────────────────

VID = 1080
V_VERSE_COLOR = (255, 248, 232)   # near-white
V_TEAL = (0, 201, 167)            # #00C9A7
V_HANDLE = "@only.jesusvibes"
V_PADDING = 84
V_DIVIDER_Y = int(VID * 0.728)
V_DIVIDER_H = 8
V_DIVIDER_HALF_W = int(VID * 0.845 / 2)

def _vfont(name, size):
    p = os.path.join(FONT_DIR, name)
    if os.path.exists(p):
        return ImageFont.truetype(p, size)
    return load_font(FALLBACK_FONT_PATH, size)

def _vshadow(draw, text, xy, font, fill, off=3, salpha=170):
    x, y = xy
    draw.text((x + off, y + off), text, font=font, fill=(0, 0, 0, salpha))
    draw.text((x, y), text, font=font, fill=fill)

def build_video_overlay(quote_text, reference):
    """Static RGBA overlay for the looping video: vignette + panel + verse + divider + ref + handle."""
    ov = Image.new("RGBA", (VID, VID), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    max_w = VID - V_PADDING * 2

    size = 60
    while size > 30:
        qf = _vfont("Montserrat-Bold.ttf", size)
        lines = wrap_text(quote_text, qf, max_w, d)
        if len(lines) <= 5:
            break
        size -= 2
    qf = _vfont("Montserrat-Bold.ttf", size)
    lines = wrap_text(quote_text, qf, max_w, d)
    line_h = int(size * 1.18)
    ref_font = _vfont("Montserrat-BoldItalic.ttf", 40)
    handle_font = _vfont("Montserrat-Regular.ttf", 28)

    block_h = line_h * len(lines)
    verse_top = V_DIVIDER_Y - 22 - block_h

    panel_top = max(0, verse_top - 60)
    panel_h = VID - panel_top
    for i in range(panel_h):
        a = int(200 * min(1.0, i / (panel_h * 0.8)))
        d.line([(0, panel_top + i), (VID, panel_top + i)], fill=(7, 4, 18, a))

    y = verse_top
    for ln in lines:
        bb = d.textbbox((0, 0), ln, font=qf)
        x = (VID - (bb[2] - bb[0])) // 2 - bb[0]
        _vshadow(d, ln, (x, y), qf, (*V_VERSE_COLOR, 255))
        y += line_h

    cx = VID // 2
    d.rectangle([(cx - V_DIVIDER_HALF_W, V_DIVIDER_Y), (cx + V_DIVIDER_HALF_W, V_DIVIDER_Y + V_DIVIDER_H)], fill=(*V_TEAL, 255))

    ref_text = f"{reference}, KJV"
    bb = d.textbbox((0, 0), ref_text, font=ref_font)
    rx = (VID - (bb[2] - bb[0])) // 2 - bb[0]
    ry = V_DIVIDER_Y + V_DIVIDER_H + 18
    _vshadow(d, ref_text, (rx, ry), ref_font, (*V_TEAL, 255))

    bb2 = d.textbbox((0, 0), V_HANDLE, font=handle_font)
    hx = (VID - (bb2[2] - bb2[0])) // 2 - bb2[0]
    hy = ry + (bb[3] - bb[1]) + 24
    _vshadow(d, V_HANDLE, (hx, hy), handle_font, (255, 255, 255, 235), off=2)

    # radial vignette
    cxg = cyg = VID / 2
    maxd = math.hypot(cxg, cyg)
    yy, xx = np.ogrid[0:VID, 0:VID]
    dist = np.sqrt((xx - cxg) ** 2 + (yy - cyg) ** 2) / maxd
    alpha = (np.clip((dist - 0.45) / 0.55, 0, 1) ** 1.4 * 165).astype("uint8")
    vignette = Image.new("RGBA", (VID, VID), (0, 0, 0, 255))
    vignette.putalpha(Image.fromarray(alpha, "L"))

    return Image.alpha_composite(vignette, ov)

def make_loop_video(video_url, quote_text, reference, target_seconds=36):
    uid = uuid.uuid4().hex
    raw = os.path.join(TEMP_DIR, f"{uid}_raw.mp4")
    boom = os.path.join(TEMP_DIR, f"{uid}_boom.mp4")
    looped = os.path.join(TEMP_DIR, f"{uid}_loop.mp4")
    ovp = os.path.join(TEMP_DIR, f"{uid}_ov.png")
    out_name = f"{uid}.mp4"
    out = os.path.join(TEMP_DIR, out_name)

    r = req_lib.get(video_url, timeout=120)
    r.raise_for_status()
    with open(raw, "wb") as f:
        f.write(r.content)

    # 1) square 1080 + boomerang (forward + reversed, drop the duplicated seam frame)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", raw,
        "-filter_complex",
        f"[0:v]scale={VID}:{VID}:force_original_aspect_ratio=increase,"
        f"crop={VID}:{VID},fps=30,setpts=PTS-STARTPTS[f];"
        f"[f]split[f1][f2];[f2]reverse,trim=start_frame=1,setpts=PTS-STARTPTS[rev];"
        f"[f1][rev]concat=n=2:v=1[v]",
        "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p", boom
    ], check=True)

    # 2) loop boomerang to target length
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "50", "-i", boom,
        "-t", str(target_seconds), "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p", looped
    ], check=True)

    # 3) burn overlay
    build_video_overlay(quote_text, reference).save(ovp)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", looped, "-i", ovp,
        "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto",
        "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", out
    ], check=True)

    for f in (raw, boom, looped, ovp):
        try:
            os.remove(f)
        except OSError:
            pass
    return out_name


def make_video(image_url, quote_text, reference):
    FPS = 24
    TOTAL_SECONDS = 12
    OVERLAY_FPS = 6
    OVERLAY_FRAMES = TOTAL_SECONDS * OVERLAY_FPS
    PROC_W, PROC_H = 540, 960
    padding = 30
    max_text_width = PROC_W - (padding * 2)

    resp = req_lib.get(image_url, timeout=30)
    resp.raise_for_status()
    base_img = Image.open(io.BytesIO(resp.content)).convert("RGB")
    base_img = base_img.resize((540, 540), Image.LANCZOS)

    uid = uuid.uuid4().hex

    dummy = Image.new("RGBA", (PROC_W, PROC_H), (0, 0, 0, 0))
    draw_dummy = ImageDraw.Draw(dummy)

    quote_font_size = 31
    quote_font = load_font(QUOTE_FONT_PATH, quote_font_size)
    while quote_font_size > 15:
        quote_font = load_font(QUOTE_FONT_PATH, quote_font_size)
        lines = wrap_text(f'"{quote_text}"', quote_font, max_text_width, draw_dummy)
        if len(lines) <= 5:
            break
        quote_font_size -= 2

    lines = wrap_text(f'"{quote_text}"', quote_font, max_text_width, draw_dummy)
    line_height = quote_font_size + 7
    ref_font = load_font(REF_FONT_PATH, 22)
    brand_font = load_font(FALLBACK_FONT_PATH, 15)

    FADE_F = int(OVERLAY_FPS * 1.2)
    HOLD_F = int(OVERLAY_FPS * 1.5)
    LINE_SLOT = FADE_F + HOLD_F
    line_starts = [i * LINE_SLOT for i in range(len(lines))]
    ref_start = (line_starts[-1] + LINE_SLOT) if lines else LINE_SLOT
    handle_start = ref_start + FADE_F + int(OVERLAY_FPS * 0.8)

    PANEL_Y = int(PROC_H * 0.53)
    TEXT_START_Y = PANEL_Y + 25

    video_filename = f"{uid}.mp4"
    video_path = os.path.join(TEMP_DIR, video_filename)

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{PROC_W}x{PROC_H}",
        "-pix_fmt", "rgb24",
        "-r", str(OVERLAY_FPS),
        "-i", "pipe:0",
        "-vf", f"fps={FPS},scale={VIDEO_W}:{VIDEO_H}:flags=lanczos",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        video_path
    ]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        for fi in range(OVERLAY_FRAMES):
            zoom = 1.0 + (fi / max(OVERLAY_FRAMES - 1, 1)) * 0.06
            zw = int(540 * zoom)
            zoomed = base_img.resize((zw, zw), Image.LANCZOS)
            x_off = (zw - 540) // 2
            cropped = zoomed.crop((x_off, x_off, x_off + 540, x_off + 540))

            fc = Image.new("RGB", (PROC_W, PROC_H), (7, 4, 18))
            fc.paste(cropped, (0, 210))

            ov = Image.new("RGBA", (PROC_W, PROC_H), (0, 0, 0, 0))
            draw = ImageDraw.Draw(ov)

            for y in range(PROC_H - PANEL_Y + 100):
                alpha = int(235 * min(1.0, y / 140))
                draw.rectangle([(0, PANEL_Y - 100 + y), (PROC_W, PANEL_Y - 99 + y)], fill=(7, 4, 18, alpha))

            draw.rectangle([(padding, TEXT_START_Y - 9), (PROC_W - padding, TEXT_START_Y - 8)], fill=(*TEAL, 255))

            current_y = TEXT_START_Y + 4
            for i, line in enumerate(lines):
                sf = line_starts[i]
                if fi < sf:
                    break
                ar = min(1.0, (fi - sf) / max(FADE_F, 1))
                bbox = draw.textbbox((0, 0), line, font=quote_font)
                x = (PROC_W - (bbox[2] - bbox[0])) // 2
                draw.text((x + 2, current_y + 2), line, font=quote_font, fill=(0, 0, 0, int(160 * ar)))
                draw.text((x, current_y), line, font=quote_font, fill=(*GOLD, int(255 * ar)))
                current_y += line_height

            if fi >= ref_start:
                ar = min(1.0, (fi - ref_start) / max(FADE_F, 1))
                ref_text = f"— {reference}, KJV"
                bbox = draw.textbbox((0, 0), ref_text, font=ref_font)
                rx = (PROC_W - (bbox[2] - bbox[0])) // 2
                draw.text((rx + 1, current_y + 10 + 1), ref_text, font=ref_font, fill=(0, 0, 0, int(160 * ar)))
                draw.text((rx, current_y + 10), ref_text, font=ref_font, fill=(*TEAL, int(255 * ar)))

            if fi >= handle_start:
                ar = min(1.0, (fi - handle_start) / max(FADE_F, 1))
                brand = "@only.jesusvibes"
                bbox = draw.textbbox((0, 0), brand, font=brand_font)
                bx = (PROC_W - (bbox[2] - bbox[0])) // 2
                draw.text((bx, PROC_H - 38), brand, font=brand_font, fill=(255, 255, 255, int(200 * ar)))

            comp = Image.alpha_composite(fc.convert("RGBA"), ov).convert("RGB")
            proc.stdin.write(comp.tobytes())

        proc.stdin.close()

    except Exception as e:
        proc.stdin.close()
        proc.kill()
        raise Exception(f"Frame generation error: {str(e)}")

    stderr_output = proc.stderr.read().decode()
    proc.wait()

    if proc.returncode != 0:
        raise Exception(f"FFmpeg error: {stderr_output[-800:]}")

    return video_filename


# ── ROUTES ───────────────────────────────────────────────────────────────────

@app.route("/compose", methods=["POST"])
def compose():
    data = request.json
    image_url  = data.get("image_url")
    quote_text = data.get("quote_text", "")
    reference  = data.get("reference", "")

    if not image_url:
        return jsonify({"error": "image_url required"}), 400

    try:
        uid = uuid.uuid4().hex
        result = compose_image(image_url, quote_text, reference)
        filename = f"{uid}.jpg"
        filepath = os.path.join(TEMP_DIR, filename)
        result.save(filepath, "JPEG", quality=95)
        composed_url = f"https://web-production-f5d29.up.railway.app/image/{filename}"
        return jsonify({"composed_url": composed_url, "filename": filename})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/compose-video", methods=["POST"])
def compose_video():
    data = request.json or {}
    video_url  = data.get("video_url")
    quote_text = data.get("quote_text", "")
    reference  = data.get("reference", "")
    seconds    = int(data.get("seconds", 36))
    seconds    = max(30, min(45, seconds))

    if not video_url:
        return jsonify({"error": "video_url required"}), 400

    try:
        filename = make_loop_video(video_url, quote_text, reference, target_seconds=seconds)
        final_url = f"https://web-production-f5d29.up.railway.app/video/{filename}"
        return jsonify({"video_url": final_url, "filename": filename, "seconds": seconds})
    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"ffmpeg failed: {e}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/upload", methods=["POST"])
def upload():
    """S3 upload route — not currently in use."""
    return jsonify({"error": "upload route not available"}), 501


@app.route("/image/<filename>", methods=["GET"])
def serve_image(filename):
    filename = os.path.basename(filename)
    filepath = os.path.join(TEMP_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "Image not found"}), 404
    return send_file(filepath, mimetype="image/jpeg")


@app.route("/video/<filename>", methods=["GET"])
def serve_video(filename):
    filename = os.path.basename(filename)
    filepath = os.path.join(TEMP_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "Video not found"}), 404
    return send_file(filepath, mimetype="video/mp4")


@app.route("/models/<filename>", methods=["GET"])
def serve_model(filename):
    filename = os.path.basename(filename)
    filepath = os.path.join("/app/models", filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "Model not found"}), 404
    return send_file(filepath, mimetype="application/octet-stream")

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
