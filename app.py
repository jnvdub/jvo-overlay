from flask import Flask, request, jsonify, send_file
from PIL import Image, ImageDraw, ImageFont
import requests
import io
import os
import uuid
import subprocess
import shutil

app = Flask(__name__)

TEMP_DIR = "/tmp/jvo_images"
os.makedirs(TEMP_DIR, exist_ok=True)

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
    response = requests.get(image_url, timeout=30)
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


def make_video(image_url, quote_text, reference):
    """
    Memory-efficient video generation using pipe approach:
    - Frames streamed directly to FFmpeg stdin — no temp files
    - Process at half resolution (540x960), FFmpeg upscales to 1080x1920
    - 6fps x 12s = 72 frames total
    """
    FPS = 24
    TOTAL_SECONDS = 12
    OVERLAY_FPS = 6
    OVERLAY_FRAMES = TOTAL_SECONDS * OVERLAY_FPS  # 72 frames
    PROC_W, PROC_H = 540, 960
    padding = 30
    max_text_width = PROC_W - (padding * 2)

    # Download base image
    resp = requests.get(image_url, timeout=30)
    resp.raise_for_status()
    base_img = Image.open(io.BytesIO(resp.content)).convert("RGB")
    base_img = base_img.resize((540, 540), Image.LANCZOS)

    uid = uuid.uuid4().hex

    # Set up fonts at half scale
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

    # Output video path
    video_filename = f"{uid}.mp4"
    video_path = os.path.join(TEMP_DIR, video_filename)

    # Start FFmpeg reading raw RGB frames from stdin
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
            # Ken Burns zoom at half res
            zoom = 1.0 + (fi / max(OVERLAY_FRAMES - 1, 1)) * 0.06
            zw = int(540 * zoom)
            zoomed = base_img.resize((zw, zw), Image.LANCZOS)
            x_off = (zw - 540) // 2
            cropped = zoomed.crop((x_off, x_off, x_off + 540, x_off + 540))

            fc = Image.new("RGB", (PROC_W, PROC_H), (7, 4, 18))
            fc.paste(cropped, (0, 210))

            ov = Image.new("RGBA", (PROC_W, PROC_H), (0, 0, 0, 0))
            draw = ImageDraw.Draw(ov)

            # Dark gradient panel
            for y in range(PROC_H - PANEL_Y + 100):
                alpha = int(235 * min(1.0, y / 140))
                draw.rectangle([(0, PANEL_Y - 100 + y), (PROC_W, PANEL_Y - 99 + y)], fill=(7, 4, 18, alpha))

            # Teal line
            draw.rectangle([(padding, TEXT_START_Y - 9), (PROC_W - padding, TEXT_START_Y - 8)], fill=(*TEAL, 255))

            # Quote lines
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

            # Reference
            if fi >= ref_start:
                ar = min(1.0, (fi - ref_start) / max(FADE_F, 1))
                ref_text = f"— {reference}, KJV"
                bbox = draw.textbbox((0, 0), ref_text, font=ref_font)
                rx = (PROC_W - (bbox[2] - bbox[0])) // 2
                draw.text((rx + 1, current_y + 10 + 1), ref_text, font=ref_font, fill=(0, 0, 0, int(160 * ar)))
                draw.text((rx, current_y + 10), ref_text, font=ref_font, fill=(*TEAL, int(255 * ar)))

            # Handle
            if fi >= handle_start:
                ar = min(1.0, (fi - handle_start) / max(FADE_F, 1))
                brand = "@only.jesusvibes"
                bbox = draw.textbbox((0, 0), brand, font=brand_font)
                bx = (PROC_W - (bbox[2] - bbox[0])) // 2
                draw.text((bx, PROC_H - 38), brand, font=brand_font, fill=(255, 255, 255, int(200 * ar)))

            # Composite and pipe raw bytes to FFmpeg
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


def build_text_overlay_image(w, h, quote_text, reference):
    """Build a PIL RGBA overlay image with text — works on any video dimensions."""
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    padding = int(w * 0.056)
    max_text_width = w - (padding * 2)

    font_size = int(w * 0.054)
    quote_font = load_font(QUOTE_FONT_PATH, font_size)
    while font_size > int(w * 0.026):
        quote_font = load_font(QUOTE_FONT_PATH, font_size)
        lines = wrap_text(f'"{quote_text}"', quote_font, max_text_width, draw)
        if len(lines) <= 5:
            break
        font_size -= 2

    lines = wrap_text(f'"{quote_text}"', quote_font, max_text_width, draw)
    line_height = font_size + int(font_size * 0.24)
    ref_font = load_font(REF_FONT_PATH, int(font_size * 0.76))
    brand_font = load_font(FALLBACK_FONT_PATH, int(font_size * 0.52))

    text_start_y = int(h * 0.52)
    panel_y = text_start_y - int(h * 0.021)
    teal_line_y = text_start_y - int(h * 0.009)

    # Dark gradient panel
    for y in range(h - panel_y):
        alpha = int(220 * min(1.0, y / max(1, h * 0.156)))
        draw.rectangle([(0, panel_y + y), (w, panel_y + y + 1)], fill=(7, 4, 18, alpha))

    # Teal accent line
    draw.rectangle([(padding, teal_line_y), (w - padding, teal_line_y + 3)], fill=(*TEAL, 255))

    # Quote lines
    current_y = text_start_y
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=quote_font)
        x = (w - (bbox[2] - bbox[0])) // 2
        draw.text((x + 3, current_y + 3), line, font=quote_font, fill=(0, 0, 0, 180))
        draw.text((x, current_y), line, font=quote_font, fill=(*GOLD, 255))
        current_y += line_height

    # Reference
    ref_text = f"— {reference}, KJV"
    bbox = draw.textbbox((0, 0), ref_text, font=ref_font)
    ref_x = (w - (bbox[2] - bbox[0])) // 2
    ref_y = current_y + int(h * 0.010)
    draw.text((ref_x + 2, ref_y + 2), ref_text, font=ref_font, fill=(0, 0, 0, 180))
    draw.text((ref_x, ref_y), ref_text, font=ref_font, fill=(*TEAL, 255))

    # Brand handle
    brand = "@only.jesusvibes"
    bbox = draw.textbbox((0, 0), brand, font=brand_font)
    bx = (w - (bbox[2] - bbox[0])) // 2
    draw.text((bx, h - int(h * 0.042)), brand, font=brand_font, fill=(255, 255, 255, 160))

    return overlay


@app.route("/overlay_video", methods=["POST"])
def overlay_video():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON body"}), 400
    video_url = data.get("video_url")
    quote_text = data.get("quote_text")
    reference = data.get("reference")
    if not all([video_url, quote_text, reference]):
        return jsonify({"error": "Missing required fields: video_url, quote_text, reference"}), 400

    uid = uuid.uuid4().hex
    input_path = os.path.join(TEMP_DIR, f"input_{uid}.mp4")
    output_path = os.path.join(TEMP_DIR, f"overlaid_{uid}.mp4")

    try:
        # Download video
        resp = requests.get(video_url, timeout=60)
        resp.raise_for_status()
        with open(input_path, "wb") as f:
            f.write(resp.content)

        TARGET_W, TARGET_H = 1080, 1920

        # Build PIL text overlay once at target dimensions
        overlay = build_text_overlay_image(TARGET_W, TARGET_H, quote_text, reference)

        # Extract ALL frames first to temp dir, then composite and encode
        frames_dir = os.path.join(TEMP_DIR, f"frames_{uid}")
        os.makedirs(frames_dir, exist_ok=True)

        extract_result = subprocess.run([
            "ffmpeg", "-y", "-i", input_path,
            "-vf", (
                f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
                f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2:color=0x070412"
            ),
            "-f", "image2", os.path.join(frames_dir, "frame_%04d.png")
        ], capture_output=True, text=True, timeout=60)

        if extract_result.returncode != 0:
            raise Exception(f"Extract error: {extract_result.stderr[-300:]}")

        frames = sorted([f for f in os.listdir(frames_dir) if f.endswith(".png")])
        if not frames:
            raise Exception("No frames extracted from video")

        # Composite each frame with PIL overlay
        comp_dir = os.path.join(TEMP_DIR, f"comp_{uid}")
        os.makedirs(comp_dir, exist_ok=True)

        for fname in frames:
            frame = Image.open(os.path.join(frames_dir, fname)).convert("RGBA")
            composited = Image.alpha_composite(frame, overlay).convert("RGB")
            composited.save(
                os.path.join(comp_dir, fname.replace(".png", ".jpg")),
                format="JPEG", quality=88
            )

        # Write concat file for reliable encoding
        concat_path = os.path.join(TEMP_DIR, f"concat_{uid}.txt")
        comp_frames = sorted([f for f in os.listdir(comp_dir) if f.endswith(".jpg")])
        fps = 24
        with open(concat_path, "w") as cf:
            for fname in comp_frames:
                fpath = os.path.join(comp_dir, fname)
                cf.write(f"file '{fpath}'\n")
                cf.write(f"duration {1.0 / fps}\n")
            if comp_frames:
                cf.write(f"file '{os.path.join(comp_dir, comp_frames[-1])}'\n")

        encode_result = subprocess.run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_path,
            "-vf", f"fps={fps}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            output_path
        ], capture_output=True, text=True, timeout=120)

        # Cleanup temp dirs
        shutil.rmtree(frames_dir, ignore_errors=True)
        shutil.rmtree(comp_dir, ignore_errors=True)
        if os.path.exists(concat_path):
            os.remove(concat_path)

        if encode_result.returncode != 0:
            raise Exception(f"Encode error: {encode_result.stderr[-400:]}")

        host = request.host_url.rstrip("/")
        filename = f"overlaid_{uid}.mp4"
        return jsonify({
            "success": True,
            "overlaid_video_url": f"{host}/video/{filename}",
            "filename": filename,
            "frames": len(comp_frames)
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(input_path):
            os.remove(input_path)


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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
