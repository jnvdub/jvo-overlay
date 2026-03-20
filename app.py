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
    Efficient video generation:
    - Overlay frames at 10fps (150 frames for 15s, not 600)
    - FFmpeg zoompan handles Ken Burns natively on base image
    - Total generation time ~20-30s on Railway
    """
    FPS = 24
    TOTAL_SECONDS = 15
    OVERLAY_FPS = 10
    OVERLAY_FRAMES = TOTAL_SECONDS * OVERLAY_FPS  # 150 frames

    padding = 60
    max_text_width = VIDEO_W - (padding * 2)

    # Download base image
    resp = requests.get(image_url, timeout=30)
    resp.raise_for_status()
    base_img = Image.open(io.BytesIO(resp.content)).convert("RGB")
    base_img = base_img.resize((1080, 1080), Image.LANCZOS)

    # Save base image for FFmpeg
    uid = uuid.uuid4().hex
    base_path = os.path.join(TEMP_DIR, f"base_{uid}.jpg")
    base_img.save(base_path, format="JPEG", quality=95)

    # Set up fonts
    dummy = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw_dummy = ImageDraw.Draw(dummy)

    quote_font_size = 62
    quote_font = load_font(QUOTE_FONT_PATH, quote_font_size)
    while quote_font_size > 30:
        quote_font = load_font(QUOTE_FONT_PATH, quote_font_size)
        lines = wrap_text(f'"{quote_text}"', quote_font, max_text_width, draw_dummy)
        if len(lines) <= 5:
            break
        quote_font_size -= 4

    lines = wrap_text(f'"{quote_text}"', quote_font, max_text_width, draw_dummy)
    line_height = quote_font_size + 14
    ref_font = load_font(REF_FONT_PATH, 44)
    brand_font = load_font(FALLBACK_FONT_PATH, 30)

    # Timing
    FADE_F = int(OVERLAY_FPS * 1.2)
    HOLD_F = int(OVERLAY_FPS * 1.5)
    LINE_SLOT = FADE_F + HOLD_F
    line_starts = [i * LINE_SLOT for i in range(len(lines))]
    ref_start = (line_starts[-1] + LINE_SLOT) if lines else LINE_SLOT
    handle_start = ref_start + FADE_F + int(OVERLAY_FPS * 0.8)

    PANEL_Y = 1020
    TEXT_START_Y = PANEL_Y + 50

    # Generate overlay frames
    frame_dir = os.path.join(TEMP_DIR, f"frames_{uid}")
    os.makedirs(frame_dir)

    for fi in range(OVERLAY_FRAMES):
        canvas = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)

        # Dark gradient panel
        for y in range(VIDEO_H - PANEL_Y + 200):
            alpha = int(235 * min(1.0, y / 280))
            draw.rectangle([(0, PANEL_Y - 200 + y), (VIDEO_W, PANEL_Y - 199 + y)], fill=(7, 4, 18, alpha))

        # Teal accent line
        draw.rectangle([(padding, TEXT_START_Y - 18), (VIDEO_W - padding, TEXT_START_Y - 15)], fill=(*TEAL, 255))

        # Quote lines
        current_y = TEXT_START_Y + 8
        for i, line in enumerate(lines):
            sf = line_starts[i]
            if fi < sf:
                break
            ar = min(1.0, (fi - sf) / max(FADE_F, 1))
            alpha = int(255 * ar)
            bbox = draw.textbbox((0, 0), line, font=quote_font)
            x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
            draw.text((x + 3, current_y + 3), line, font=quote_font, fill=(0, 0, 0, int(160 * ar)))
            draw.text((x, current_y), line, font=quote_font, fill=(*GOLD, alpha))
            current_y += line_height

        # Reference
        if fi >= ref_start:
            ar = min(1.0, (fi - ref_start) / max(FADE_F, 1))
            alpha = int(255 * ar)
            ref_text = f"— {reference}, KJV"
            bbox = draw.textbbox((0, 0), ref_text, font=ref_font)
            rx = (VIDEO_W - (bbox[2] - bbox[0])) // 2
            draw.text((rx + 2, current_y + 18 + 2), ref_text, font=ref_font, fill=(0, 0, 0, int(160 * ar)))
            draw.text((rx, current_y + 18), ref_text, font=ref_font, fill=(*TEAL, alpha))

        # Handle
        if fi >= handle_start:
            ar = min(1.0, (fi - handle_start) / max(FADE_F, 1))
            alpha = int(200 * ar)
            brand = "@only.jesusvibes"
            bbox = draw.textbbox((0, 0), brand, font=brand_font)
            bx = (VIDEO_W - (bbox[2] - bbox[0])) // 2
            draw.text((bx, VIDEO_H - 75), brand, font=brand_font, fill=(255, 255, 255, alpha))

        canvas.save(os.path.join(frame_dir, f"frame_{fi:04d}.png"), format="PNG")

    # Encode with FFmpeg
    video_filename = f"{uid}.mp4"
    video_path = os.path.join(TEMP_DIR, video_filename)
    total_output_frames = TOTAL_SECONDS * FPS

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", base_path,
        "-framerate", str(OVERLAY_FPS), "-i", os.path.join(frame_dir, "frame_%04d.png"),
        "-filter_complex",
        f"[0:v]scale=1080:1080,pad=1080:1920:0:420:color=#070412,"
        f"zoompan=z='min(zoom+0.0004,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={total_output_frames}:s=1080x1920:fps={FPS}[base];"
        f"[1:v]scale=1080:1920[overlay];"
        f"[base][overlay]overlay=0:0:format=yuv420[out]",
        "-map", "[out]",
        "-t", str(TOTAL_SECONDS),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        video_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    # Cleanup
    shutil.rmtree(frame_dir, ignore_errors=True)
    if os.path.exists(base_path):
        os.remove(base_path)

    if result.returncode != 0:
        raise Exception(f"FFmpeg error: {result.stderr[-500:]}")

    return video_filename


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "JVO Image Overlay + Video",
        "fonts": {"quote": QUOTE_FONT_PATH, "ref": REF_FONT_PATH, "fallback": FALLBACK_FONT_PATH}
    })

@app.route("/compose", methods=["POST"])
def compose():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON body"}), 400
    image_url = data.get("image_url")
    quote_text = data.get("quote_text")
    reference = data.get("reference")
    if not all([image_url, quote_text, reference]):
        return jsonify({"error": "Missing required fields: image_url, quote_text, reference"}), 400
    try:
        img = compose_image(image_url, quote_text, reference)
        filename = f"{uuid.uuid4()}.jpg"
        filepath = os.path.join(TEMP_DIR, filename)
        img.save(filepath, format="JPEG", quality=95)
        host = request.host_url.rstrip('/')
        return jsonify({"success": True, "composed_url": f"{host}/image/{filename}", "filename": filename})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/make_video", methods=["POST"])
def make_video_endpoint():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON body"}), 400
    image_url = data.get("image_url")
    quote_text = data.get("quote_text")
    reference = data.get("reference")
    if not all([image_url, quote_text, reference]):
        return jsonify({"error": "Missing required fields: image_url, quote_text, reference"}), 400
    try:
        video_filename = make_video(image_url, quote_text, reference)
        host = request.host_url.rstrip('/')
        return jsonify({"success": True, "video_url": f"{host}/video/{video_filename}", "filename": video_filename})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
