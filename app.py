from flask import Flask, request, jsonify, send_file
from PIL import Image, ImageDraw, ImageFont
import requests as req_lib
import io
import os
import uuid
import subprocess
import shutil

app = Flask(__name__)

TEMP_DIR = "/tmp/jvo_images"
os.makedirs(TEMP_DIR, exist_ok=True)

# ── S3 CLIENT ────────────────────────────────────────────────────────────────
S3_ENDPOINT   = "https://t3.storageapi.dev"
S3_BUCKET     = "orderly-flask-tlkgjhixwkd"
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")

s3_client = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    config=Config(signature_version="s3"),
    region_name="auto"
)

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


@app.route("/upload", methods=["POST"])
def upload():
    data = request.json
    url          = data.get("url")
    filename     = data.get("filename")
    content_type = data.get("content_type", "image/jpeg")

    if not url or not filename:
        return jsonify({"error": "url and filename required"}), 400

    try:
        # Download from source
        response = req_lib.get(url, timeout=60)
        response.raise_for_status()

        # Upload to S3 using AWS Signature V4
        import hmac
        import hashlib
        from datetime import datetime, timezone

        file_content = response.content
        now = datetime.now(timezone.utc)
        date_str = now.strftime('%Y%m%d')
        datetime_str = now.strftime('%Y%m%dT%H%M%SZ')

        host = 't3.storageapi.dev'
        region = 'auto'
        service = 's3'

        # Build canonical request
        content_hash = hashlib.sha256(file_content).hexdigest()
        canonical_headers = f'content-type:{content_type}\nhost:{host}\nx-amz-acl:public-read\nx-amz-content-sha256:{content_hash}\nx-amz-date:{datetime_str}\n'
        signed_headers = 'content-type;host;x-amz-acl;x-amz-content-sha256;x-amz-date'
        canonical_request = f'PUT\n/{S3_BUCKET}/{filename}\n\n{canonical_headers}\n{signed_headers}\n{content_hash}'

        # Build string to sign
        credential_scope = f'{date_str}/{region}/{service}/aws4_request'
        string_to_sign = f'AWS4-HMAC-SHA256\n{datetime_str}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}'

        # Calculate signature
        def sign(key, msg):
            return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

        signing_key = sign(sign(sign(sign(f'AWS4{S3_SECRET_KEY}'.encode('utf-8'), date_str), region), service), 'aws4_request')
        signature = hmac.new(signing_key, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()

        authorization = f'AWS4-HMAC-SHA256 Credential={S3_ACCESS_KEY}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}'

        headers = {
            'Content-Type': content_type,
            'x-amz-acl': 'public-read',
            'x-amz-content-sha256': content_hash,
            'x-amz-date': datetime_str,
            'Authorization': authorization
        }

        put_response = req_lib.put(
            f'https://{host}/{S3_BUCKET}/{filename}',
            data=file_content,
            headers=headers,
            timeout=60
        )
        put_response.raise_for_status()

        public_url = f'https://{host}/{S3_BUCKET}/{filename}'
        return jsonify({"url": public_url})

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
