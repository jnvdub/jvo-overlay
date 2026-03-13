from flask import Flask, request, jsonify, send_file
from PIL import Image, ImageDraw, ImageFont
import requests
import io
import os
import uuid

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

GOLD = (255, 209, 102, 255)
TEAL = (0, 201, 167, 255)
CANVAS_SIZE = (1080, 1080)

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
    panel_height = 340
    panel_top = CANVAS_SIZE[1] - panel_height
    for y in range(panel_height):
        alpha = int(210 * (y / panel_height))
        draw.rectangle([(0, panel_top + y), (CANVAS_SIZE[0], panel_top + y + 1)], fill=(7, 4, 18, alpha))
    line_y = panel_top + 20
    draw.rectangle([(padding, line_y), (CANVAS_SIZE[0] - padding, line_y + 3)], fill=TEAL)
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
    quote_y = panel_top + 50
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=quote_font)
        x = (CANVAS_SIZE[0] - (bbox[2] - bbox[0])) // 2
        draw_text_with_shadow(draw, line, (x, quote_y), quote_font, GOLD)
        quote_y += line_height
    ref_font = load_font(REF_FONT_PATH, 38)
    ref_text = f"— {reference}, KJV"
    bbox = draw.textbbox((0, 0), ref_text, font=ref_font)
    ref_x = (CANVAS_SIZE[0] - (bbox[2] - bbox[0])) // 2
    ref_y = quote_y + 16
    draw_text_with_shadow(draw, ref_text, (ref_x, ref_y), ref_font, TEAL)
    brand_font = load_font(FALLBACK_FONT_PATH, 24)
    brand_text = "@only.jesusvibes"
    bbox = draw.textbbox((0, 0), brand_text, font=brand_font)
    brand_x = (CANVAS_SIZE[0] - (bbox[2] - bbox[0])) // 2
    brand_y = ref_y + 52
    draw_text_with_shadow(draw, brand_text, (brand_x, brand_y), brand_font, (255, 255, 255, 160))
    result = Image.alpha_composite(base_img, overlay).convert("RGB")
    return result

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "JVO Image Overlay", "fonts": {"quote": QUOTE_FONT_PATH, "ref": REF_FONT_PATH, "fallback": FALLBACK_FONT_PATH}})

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
        composed_url = f"{host}/image/{filename}"
        return jsonify({"success": True, "composed_url": composed_url, "filename": filename})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/image/<filename>", methods=["GET"])
def serve_image(filename):
    filename = os.path.basename(filename)
    filepath = os.path.join(TEMP_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "Image not found"}), 404
    return send_file(filepath, mimetype="image/jpeg")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
