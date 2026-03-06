import os
import sqlite3
from datetime import datetime
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    send_from_directory,
    send_file,
)
from werkzeug.utils import secure_filename
from PIL import Image, ImageDraw, ImageFont

APP_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(APP_DIR, "inventory.db")
UPLOAD_FOLDER = os.path.join(APP_DIR, "uploads")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
SECTIONS = ["Indoor", "Outdoor"]
MODELS = ["12C", "12H", "18C", "18H"]

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "inventory-secret-key")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024  # 6MB

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section TEXT NOT NULL,
            model TEXT NOT NULL,
            item_name TEXT NOT NULL,
            code TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            image_filename TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    cols = [r["name"] for r in cur.execute("PRAGMA table_info(items)").fetchall()]

    if "quantity" not in cols:
        cur.execute("ALTER TABLE items ADD COLUMN quantity INTEGER NOT NULL DEFAULT 0")

    if "item_name" not in cols:
        cur.execute("ALTER TABLE items ADD COLUMN item_name TEXT")
        cur.execute("UPDATE items SET item_name='—' WHERE item_name IS NULL")

    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_items_code_unique ON items(code)")

    conn.commit()
    conn.close()


# مهم جدًا علشان Railway / gunicorn
init_db()


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_search_query():
    return (request.args.get("q") or "").strip()


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/", methods=["GET"])
def index():
    q = get_search_query()

    conn = get_db()
    if q:
        like = f"%{q}%"
        items = conn.execute(
            """
            SELECT * FROM items
            WHERE code LIKE ? OR item_name LIKE ?
            ORDER BY id DESC
            """,
            (like, like),
        ).fetchall()
    else:
        items = conn.execute("SELECT * FROM items ORDER BY id DESC").fetchall()
    conn.close()

    return render_template(
        "index.html",
        items=items,
        sections=SECTIONS,
        models=MODELS,
        q=q,
    )


@app.route("/filter", methods=["GET"])
def filter_page():
    q = get_search_query()
    return render_template("filter.html", q=q)


@app.route("/add", methods=["POST"])
def add_item():
    section = (request.form.get("section") or "").strip()
    model = (request.form.get("model") or "").strip()
    item_name = (request.form.get("item_name") or "").strip()
    code = (request.form.get("code") or "").strip()

    qty_raw = (request.form.get("quantity") or "0").strip()
    try:
        quantity = int(qty_raw)
        if quantity < 0:
            quantity = 0
    except ValueError:
        quantity = 0

    if section not in SECTIONS:
        flash("اختار القسم صح.", "error")
        return redirect(url_for("index"))

    if model not in MODELS:
        flash("اختار الموديل صح.", "error")
        return redirect(url_for("index"))

    if not item_name:
        flash("اكتب اسم الصنف.", "error")
        return redirect(url_for("index"))

    if not code:
        flash("اكتب الكود.", "error")
        return redirect(url_for("index"))

    conn = get_db()
    exists = conn.execute("SELECT 1 FROM items WHERE code = ? LIMIT 1", (code,)).fetchone()
    if exists:
        conn.close()
        flash("الكود ده موجود قبل كده، لازم كود مختلف.", "error")
        return redirect(url_for("index"))

    image_filename = None
    file = request.files.get("image")

    if file and file.filename:
        if not allowed_file(file.filename):
            conn.close()
            flash("الصورة لازم تكون PNG أو JPG أو JPEG أو WEBP.", "error")
            return redirect(url_for("index"))

        safe_name = secure_filename(file.filename)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        image_filename = f"{ts}_{safe_name}"
        save_path = os.path.join(app.config["UPLOAD_FOLDER"], image_filename)
        file.save(save_path)

    try:
        conn.execute(
            """
            INSERT INTO items (section, model, item_name, code, quantity, image_filename, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                section,
                model,
                item_name,
                code,
                quantity,
                image_filename,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
        flash("تم إضافة الصنف بنجاح.", "ok")
    except sqlite3.IntegrityError:
        flash("الكود ده موجود قبل كده، لازم كود مختلف.", "error")
    finally:
        conn.close()

    return redirect(url_for("index"))


@app.route("/qty/<int:item_id>", methods=["POST"])
def update_qty(item_id):
    q = (request.args.get("q") or "").strip()
    action = (request.form.get("action") or "").strip()

    conn = get_db()
    row = conn.execute("SELECT quantity FROM items WHERE id = ?", (item_id,)).fetchone()

    if not row:
        conn.close()
        flash("الصنف مش موجود.", "error")
        return redirect(url_for("index", q=q))

    qty = int(row["quantity"] or 0)

    if action == "inc":
        qty += 1
    elif action == "dec":
        qty = max(0, qty - 1)
    elif action == "set":
        raw_value = (request.form.get("value") or "0").strip()
        try:
            qty = max(0, int(raw_value))
        except ValueError:
            pass

    conn.execute("UPDATE items SET quantity = ? WHERE id = ?", (qty, item_id))
    conn.commit()
    conn.close()

    flash("تم تعديل الكمية.", "ok")
    return redirect(url_for("index", q=q))


@app.route("/delete/<int:item_id>", methods=["POST"])
def delete_item(item_id):
    q = (request.args.get("q") or "").strip()

    conn = get_db()
    row = conn.execute("SELECT image_filename FROM items WHERE id = ?", (item_id,)).fetchone()
    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()

    if row and row["image_filename"]:
        image_path = os.path.join(app.config["UPLOAD_FOLDER"], row["image_filename"])
        if os.path.exists(image_path):
            try:
                os.remove(image_path)
            except OSError:
                pass

    flash("تم حذف الصنف.", "ok")
    return redirect(url_for("index", q=q))


def load_font(size: int):
    candidates = [
        os.path.join(APP_DIR, "arial.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


@app.route("/export.png", methods=["GET"])
def export_png():
    q = get_search_query()

    conn = get_db()
    if q:
        like = f"%{q}%"
        items = conn.execute(
            """
            SELECT * FROM items
            WHERE code LIKE ? OR item_name LIKE ?
            ORDER BY id DESC
            """,
            (like, like),
        ).fetchall()
    else:
        items = conn.execute("SELECT * FROM items ORDER BY id DESC").fetchall()
    conn.close()

    width = 1400
    row_h = 150
    header_h = 150
    padding = 20
    thumb_size = (110, 110)
    height = header_h + max(1, len(items)) * row_h + 40

    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    title_font = load_font(38)
    header_font = load_font(24)
    text_font = load_font(22)
    small_font = load_font(18)

    draw.text((padding, 20), "Inventory Report", fill="black", font=title_font)
    draw.text((padding, 70), datetime.now().strftime("%Y-%m-%d %H:%M"), fill="gray", font=small_font)
    if q:
        draw.text((padding, 100), f"Search: {q}", fill="gray", font=small_font)

    y = 130
    draw.line((padding, y, width - padding, y), fill="black", width=2)

    cols = {
        "section": 20,
        "model": 180,
        "name": 320,
        "code": 640,
        "qty": 890,
        "img": 1030,
    }

    y += 15
    draw.text((cols["section"], y), "Section", fill="black", font=header_font)
    draw.text((cols["model"], y), "Model", fill="black", font=header_font)
    draw.text((cols["name"], y), "Item Name", fill="black", font=header_font)
    draw.text((cols["code"], y), "Code", fill="black", font=header_font)
    draw.text((cols["qty"], y), "Qty", fill="black", font=header_font)
    draw.text((cols["img"], y), "Image", fill="black", font=header_font)

    y += 40
    draw.line((padding, y, width - padding, y), fill="black", width=2)

    start_y = y + 15

    if not items:
        draw.text((padding, start_y + 20), "No data found.", fill="gray", font=header_font)
    else:
        for i, item in enumerate(items):
            row_y = start_y + i * row_h

            if i % 2 == 0:
                draw.rectangle(
                    (padding, row_y - 5, width - padding, row_y + row_h - 15),
                    fill=(248, 248, 248),
                )

            draw.text((cols["section"], row_y + 10), str(item["section"]), fill="black", font=text_font)
            draw.text((cols["model"], row_y + 10), str(item["model"]), fill="black", font=text_font)
            draw.text((cols["name"], row_y + 10), str(item["item_name"]), fill="black", font=text_font)
            draw.text((cols["code"], row_y + 10), str(item["code"]), fill="black", font=text_font)
            draw.text((cols["qty"], row_y + 10), str(item["quantity"]), fill="black", font=text_font)

            thumb_x = cols["img"]
            thumb_y = row_y + 5
            draw.rectangle(
                (thumb_x, thumb_y, thumb_x + thumb_size[0], thumb_y + thumb_size[1]),
                outline="black",
                width=2,
            )

            if item["image_filename"]:
                image_path = os.path.join(app.config["UPLOAD_FOLDER"], item["image_filename"])
                if os.path.exists(image_path):
                    try:
                        im2 = Image.open(image_path).convert("RGB")
                        im2.thumbnail(thumb_size)
                        ox = thumb_x + (thumb_size[0] - im2.size[0]) // 2
                        oy = thumb_y + (thumb_size[1] - im2.size[1]) // 2
                        img.paste(im2, (ox, oy))
                    except Exception:
                        draw.text((thumb_x + 8, thumb_y + 40), "Error", fill="red", font=small_font)

            draw.line(
                (padding, row_y + row_h - 15, width - padding, row_y + row_h - 15),
                fill=(210, 210, 210),
                width=2,
            )

    output_path = os.path.join(APP_DIR, "inventory_export.png")
    img.save(output_path, "PNG")
    return send_file(output_path, mimetype="image/png", as_attachment=True, download_name="inventory_export.png")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)