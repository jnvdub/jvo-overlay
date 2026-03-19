from flask import Flask, request, jsonify, send_file
from PIL import Image, ImageDraw, ImageFont
import requests
import io
import os
import uuid
import subprocess
import textwrap

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

GOLD = (255, 248, 232, 255)   # #FFF8E8 warm cream
TEAL = (0, 201, 167, 255)
CANVAS_SIZE = (1080, 1080)
VIDEO_W, VIDEO_H = 1080, 1920  # 9:16 TikTok

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
    lines = []
    current_line = []
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
        draw_text_with_shadow(draw, line, (x, quote_y), quote_font, GOLD)
        quote_y += line_height

    line_y = quote_y + 20
    draw.rectangle([(padding, line_y), (CANVAS_SIZE[0] - padding, line_y + 3)], fill=TEAL)

    ref_text = f"— {reference}, KJV"
    bbox = draw.textbbox((0, 0), ref_text, font=ref_font)
    ref_x = (CANVAS_SIZE[0] - (bbox[2] - bbox[0])) // 2
    ref_y = line_y + 16
    draw_text_with_shadow(draw, ref_text, (ref_x, ref_y), ref_font, TEAL)

    brand_text = "@only.jesusvibes"
    bbox = draw.textbbox((0, 0), brand_text, font=brand_font)
    brand_x = (CANVAS_SIZE[0] - (bbox[2] - bbox[0])) // 2
    brand_y = ref_y + 52
    draw_text_with_shadow(draw, brand_text, (brand_x, brand_y), brand_font, (255, 255, 255, 160))

    result = Image.alpha_composite(base_img, overlay).convert("RGB")
    return result


def make_video_frames(image_url, quote_text, reference):
    """
    Build a 9:16 video as a sequence of PNG frames, then encode with FFmpeg.

    Layout (1080x1920):
      - Top half (~960px): base image, Ken Burns slow zoom
      - Bottom half (~960px): dark panel with text fading in line by line

    Timing:
      - 30 fps, total ~20 seconds
      - Image zooms slowly throughout
      - Each quote line fades in over 1.5s, held for 1.5s before next
      - Reference fades in after all lines
      - Handle fades in last
      - Everything held for 2s at end
    """
    FPS = 30
    TOTAL_SECONDS = 20
    TOTAL_FRAMES = FPS * TOTAL_SECONDS

    # Download base image
    resp = requests.get(image_url, timeout=30)
    resp.raise_for_status()
    base_img = Image.open(io.BytesIO(resp.content)).convert("RGB")

    # Prepare fonts for video (slightly larger for 9:16)
    padding = 60
    max_text_width = VIDEO_W - (padding * 2)

    dummy = Image.new("RGB", (VIDEO_W, VIDEO_H))
    draw_dummy = ImageDraw.Draw(dummy)

    quote_font_size = 58
    quote_font = load_font(QUOTE_FONT_PATH, quote_font_size)
    while quote_font_size > 30:
        quote_font = load_font(QUOTE_FONT_PATH, quote_font_size)
        lines = wrap_text(f'"{quote_text}"', quote_font, max_text_width, draw_dummy)
        if len(lines) <= 5:
            break
        quote_font_size -= 4

    lines = wrap_text(f'"{quote_text}"', quote_font, max_text_width, draw_dummy)
    line_height = quote_font_size + 14
    ref_font = load_font(REF_FONT_PATH, 42)
    brand_font = load_font(FALLBACK_FONT_PATH, 28)

    # Timing plan: each line fades in then holds
    FADE_FRAMES = int(FPS * 1.5)   # 45 frames to fade in
    HOLD_FRAMES = int(FPS * 1.5)   # 45 frames hold before next line
    LINE_SLOT = FADE_FRAMES + HOLD_FRAMES  # 90 frames per line

    # Frame at which each line starts fading in
    line_start_frames = [i * LINE_SLOT for i in range(len(lines))]
    ref_start = line_start_frames[-1] + LINE_SLOT if lines else LINE_SLOT
    handle_start = ref_start + FADE_FRAMES + int(FPS * 1.0)
    end_hold_start = handle_start + FADE_FRAMES

    # Image area: top 1080x1080 of the 1080x1920 canvas
    IMG_Y_START = 0
    IMG_H = 1080

    # Text panel: bottom 840px
    PANEL_Y = IMG_H - 60  # slight overlap for smooth gradient
    TEXT_START_Y = PANEL_Y + 60

    # Ken Burns: zoom from 1.0 to 1.08 over full duration
    ZOOM_START = 1.0
    ZOOM_END = 1.08

    frame_dir = os.path.join(TEMP_DIR, f"frames_{uuid.uuid4().hex}")
    os.makedirs(frame_dir, exist_ok=True)

    for frame_idx in range(TOTAL_FRAMES):
        canvas = Image.new("RGB", (VIDEO_W, VIDEO_H), (7, 4, 18))

        # --- Ken Burns zoom on base image ---
        t = frame_idx / max(TOTAL_FRAMES - 1, 1)
        zoom = ZOOM_START + (ZOOM_END - ZOOM_START) * t
        zoomed_w = int(1080 * zoom)
        zoomed_h = int(1080 * zoom)
        zoomed = base_img.resize((zoomed_w, zoomed_h), Image.LANCZOS)
        # Crop center 1080x1080
        x_off = (zoomed_w - 1080) // 2
        y_off = (zoomed_h - 1080) // 2
        cropped = zoomed.crop((x_off, y_off, x_off + 1080, y_off + 1080))
        canvas.paste(cropped, (0, IMG_Y_START))

        # --- Dark gradient panel over bottom of image + text area ---
        overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        grad_start = PANEL_Y - 200
        grad_height = VIDEO_H - grad_start
        for y in range(grad_height):
            alpha = int(230 * min(1.0, y / 300))
            draw.rectangle([(0, grad_start + y), (VIDEO_W, grad_start + y + 1)], fill=(7, 4, 18, alpha))

        canvas_rgba = canvas.convert("RGBA")
        canvas_rgba = Image.alpha_composite(canvas_rgba, overlay)
        draw = ImageDraw.Draw(canvas_rgba)

        # --- Teal accent line ---
        teal_line_y = TEXT_START_Y - 20
        draw.rectangle([(padding, teal_line_y), (VIDEO_W - padding, teal_line_y + 3)], fill=TEAL)

        # --- Quote lines fade in by line ---
        current_y = TEXT_START_Y + 10
        for i, line in enumerate(lines):
            start_f = line_start_frames[i]
            if frame_idx < start_f:
                break  # this line hasn't started yet
            # Alpha for this line
            elapsed = frame_idx - start_f
            alpha_ratio = min(1.0, elapsed / FADE_FRAMES)
            alpha = int(255 * alpha_ratio)

            bbox = draw.textbbox((0, 0), line, font=quote_font)
            x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
            # Shadow
            draw.text((x + 3, current_y + 3), line, font=quote_font, fill=(0, 0, 0, int(180 * alpha_ratio)))
            # Text
            draw.text((x, current_y), line, font=quote_font,
                      fill=(GOLD[0], GOLD[1], GOLD[2], alpha))
            current_y += line_height

        # --- Reference fade in ---
        if frame_idx >= ref_start:
            elapsed = frame_idx - ref_start
            alpha_ratio = min(1.0, elapsed / FADE_FRAMES)
            alpha = int(255 * alpha_ratio)
            ref_text = f"— {reference}, KJV"
            bbox = draw.textbbox((0, 0), ref_text, font=ref_font)
            ref_x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
            ref_y = current_y + 20
            draw.text((ref_x + 2, ref_y + 2), ref_text, font=ref_font, fill=(0, 0, 0, int(180 * alpha_ratio)))
            draw.text((ref_x, ref_y), ref_text, font=ref_font,
                      fill=(TEAL[0], TEAL[1], TEAL[2], alpha))

        # --- Handle fade in ---
        if frame_idx >= handle_start:
            elapsed = frame_idx - handle_start
            alpha_ratio = min(1.0, elapsed / FADE_FRAMES)
            alpha = int(200 * alpha_ratio)
            brand_text = "@only.jesusvibes"
            bbox = draw.textbbox((0, 0), brand_text, font=brand_font)
            brand_x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
            brand_y = VIDEO_H - 80
            draw.text((brand_x, brand_y), brand_text, font=brand_font,
                      fill=(255, 255, 255, alpha))

        # Save frame
        frame_path = os.path.join(frame_dir, f"frame_{frame_idx:05d}.png")
        canvas_rgba.convert("RGB").save(frame_path, format="PNG")

    return frame_dir


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
def make_video():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON body"}), 400
    image_url = data.get("image_url")
    quote_text = data.get("quote_text")
    reference = data.get("reference")
    if not all([image_url, quote_text, reference]):
        return jsonify({"error": "Missing required fields: image_url, quote_text, reference"}), 400

    frame_dir = None
    try:
        # Generate frames
        frame_dir = make_video_frames(image_url, quote_text, reference)

        # Encode with FFmpeg
        video_filename = f"{uuid.uuid4()}.mp4"
        video_path = os.path.join(TEMP_DIR, video_filename)

        cmd = [
            "ffmpeg", "-y",
            "-framerate", "30",
            "-i", os.path.join(frame_dir, "frame_%05d.png"),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise Exception(f"FFmpeg error: {result.stderr[-500:]}")

        host = request.host_url.rstrip('/')
        video_url = f"{host}/video/{video_filename}"
        return jsonify({"success": True, "video_url": video_url, "filename": video_filename})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        # Clean up frames
        if frame_dir and os.path.exists(frame_dir):
            import shutil
            shutil.rmtree(frame_dir, ignore_errors=True)

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
