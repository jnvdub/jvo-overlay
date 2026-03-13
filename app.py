from flask import Flask, request, jsonify, send_file
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import requests
import textwrap
import io
import os

app = Flask(__name__)

# Font paths
QUOTE_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSerifCondensed-BoldItalic.ttf"
REF_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSerifCondensed-Bold.ttf"
FALLBACK_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# Brand colors
GOLD = (255, 209, 102, 255)        # #FFD166 - quote text
WHITE = (255, 248, 232, 255)       # #FFF8E8 - warm cream
TEAL = (0, 201, 167, 255)          # #00C9A7 - reference
SHADOW = (7, 4, 18, 200)           # Deep space black, semi-transparent

CANVAS_SIZE = (1080, 1080)

def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except:
        return ImageFont.truetype(FALLBACK_FONT_PATH, size)

def draw_text_with_shadow(draw, text, position, font, text_color, shadow_color=(0,0,0,180), shadow_offset=3):
    x, y = position
    # Draw shadow
    draw.text((x + shadow_offset, y + shadow_offset), text, font=font, fill=shadow_color)
    # Draw text
    draw.text((x, y), text, font=font, fill=text_color)

def wrap_text(text, font, max_width, draw):
    """Wrap text to fit within max_width pixels."""
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
    # Download the base image
    response = requests.get(image_url, timeout=30)
    response.raise_for_status()
    
    base_img = Image.open(io.BytesIO(response.content)).convert("RGBA")
    base_img = base_img.resize(CANVAS_SIZE, Image.LANCZOS)
    
    # Create overlay layer
    overlay = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    
    padding = 60
    max_text_width = CANVAS_SIZE[0] - (padding * 2)
    
    # --- BOTTOM GRADIENT PANEL ---
    panel_height = 340
    panel_top = CANVAS_SIZE[1] - panel_height
    
    for y in range(panel_height):
        alpha = int(210 * (y / panel_height))
        draw.rectangle(
            [(0, panel_top + y), (CANVAS_SIZE[0], panel_top + y + 1)],
            fill=(7, 4, 18, alpha)
        )
    
    # --- TEAL ACCENT LINE ---
    line_y = panel_top + 20
    draw.rectangle([(padding, line_y), (CANVAS_SIZE[0] - padding, line_y + 3)], fill=TEAL)

    # --- QUOTE TEXT ---
    quote_font_size = 52
    quote_font = load_font(QUOTE_FONT_PATH, quote_font_size)
    
    # Shrink font if needed
    while quote_font_size > 28:
        quote_font = load_font(QUOTE_FONT_PATH, quote_font_size)
        lines = wrap_text(f'"{quote_text}"', quote_font, max_text_width, draw)
        if len(lines) <= 4:
            break
        quote_font_size -= 4
    
    lines = wrap_text(f'"{quote_text}"', quote_font, max_text_width, draw)
    
    # Calculate total quote block height
    line_height = quote_font_size + 10
    total_quote_height = len(lines) * line_height
    
    # Position quote — start from panel_top + 40
    quote_y = panel_top + 50
    
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=quote_font)
        line_width = bbox[2] - bbox[0]
        x = (CANVAS_SIZE[0] - line_width) // 2
        draw_text_with_shadow(draw, line, (x, quote_y), quote_font, GOLD)
        quote_y += line_height
    
    # --- REFERENCE TEXT ---
    ref_font = load_font(REF_FONT_PATH, 38)
    ref_text = f"— {reference}, KJV"
    bbox = draw.textbbox((0, 0), ref_text, font=ref_font)
    ref_width = bbox[2] - bbox[0]
    ref_x = (CANVAS_SIZE[0] - ref_width) // 2
    ref_y = quote_y + 16
    draw_text_with_shadow(draw, ref_text, (ref_x, ref_y), ref_font, TEAL)
    
    # --- BRANDING ---
    brand_font = load_font(FALLBACK_FONT_PATH, 24)
    brand_text = "@only.jesusvibes"
    brand_bbox = draw.textbbox((0, 0), brand_text, font=brand_font)
    brand_x = (CANVAS_SIZE[0] - (brand_bbox[2] - brand_bbox[0])) // 2
    brand_y = ref_y + 52
    draw_text_with_shadow(draw, brand_text, (brand_x, brand_y), brand_font, (255, 255, 255, 160))
    
    # Composite layers
    result = Image.alpha_composite(base_img, overlay)
    result = result.convert("RGB")
    
    # Return as bytes
    output = io.BytesIO()
    result.save(output, format="JPEG", quality=95)
    output.seek(0)
    return output

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "JVO Image Overlay"})

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
        image_bytes = compose_image(image_url, quote_text, reference)
        return send_file(
            image_bytes,
            mimetype="image/jpeg",
            as_attachment=False,
            download_name="jvo_post.jpg"
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
