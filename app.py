import os
import sqlite3
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for,
    send_file, flash, send_from_directory
)
from werkzeug.utils import secure_filename
from PIL import Image, ImageDraw, ImageFont

APP_DIR = os.path.abspath(os.path.dirname(__file__))

DATA_DIR = os.environ.get("DATA_DIR") or APP_DIR   # على Render هنخليه /var/data
DB_PATH = os.path.join(DATA_DIR, "inventory.db")
UPLOAD_FOLDER = os.path.join(DATA_DIR, "uploads")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

# اختيارات ثابتة
SECTIONS = ["Indoor", "Outdoor"]
MODELS = ["12C", "12H", "18C", "18H"]

app = Flask(__name__)
app.secret_key = "change-this-secret"
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

    # Create table (لو جديد)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section TEXT NOT NULL,
            model TEXT NOT NULL,
            item_name TEXT,
            code TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            image_filename TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    # Migrations (لو DB قديم)
    cols = [r["name"] for r in cur.execute("PRAGMA table_info(items)").fetchall()]

    if "quantity" not in cols:
        cur.execute("ALTER TABLE items ADD COLUMN quantity INTEGER NOT NULL DEFAULT 0")

    if "item_name" not in cols:
        cur.execute("ALTER TABLE items ADD COLUMN item_name TEXT")
        cur.execute("UPDATE items SET item_name='—' WHERE item_name IS NULL")

    # ✅ امنع تكرار الكود نهائيًا
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_items_code_unique ON items(code)")

    conn.commit()
    conn.close()


def allowed_file(filename: str) -> bool:
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_EXTENSIONS


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    # حل مشكلة الصور
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


def build_filters_from_args(args):
    # فلتر واحد فقط: q (كود أو اسم الصنف)
    q = (args.get("q") or "").strip()
    return {"q": q}


@app.route("/", methods=["GET"])
def index():
    f = build_filters_from_args(request.args)
    q = f["q"]

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
        filters=f
    )


@app.route("/filter", methods=["GET"])
def filter_page():
    f = build_filters_from_args(request.args)
    return render_template("filter.html", filters=f)


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
    except:
        quantity = 0

    if section not in SECTIONS:
        flash("اختار القسم صح (Indoor/Outdoor).", "error")
        return redirect(url_for("index"))

    if model not in MODELS:
        flash("اختار الموديل صح (12C/12H/18C/18H).", "error")
        return redirect(url_for("index"))

    if not item_name:
        flash("من فضلك اكتب اسم الصنف.", "error")
        return redirect(url_for("index"))

    if not code:
        flash("من فضلك اكتب الكود.", "error")
        return redirect(url_for("index"))

    conn = get_db()

    # ✅ تأكد الكود مش متكرر (رسالة واضحة)
    exists = conn.execute("SELECT 1 FROM items WHERE code = ? LIMIT 1", (code,)).fetchone()
    if exists:
        conn.close()
        flash("الكود ده موجود قبل كده ❌ لازم كود مختلف.", "error")
        return redirect(url_for("index"))

    image_filename = None
    file = request.files.get("image")

    if file and file.filename:
        if not allowed_file(file.filename):
            conn.close()
            flash("الصورة لازم تكون PNG/JPG/JPEG/WEBP.", "error")
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
            (section, model, item_name, code, quantity, image_filename, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # احتياط لو الـ UNIQUE INDEX مسكها
        flash("الكود ده موجود قبل كده ❌ لازم كود مختلف.", "error")
        conn.close()
        return redirect(url_for("index"))
    finally:
        conn.close()

    flash("تم إضافة الصنف ✅", "ok")
    return redirect(url_for("index"))


@app.route("/qty/<int:item_id>", methods=["POST"])
def update_qty(item_id):
    action = (request.form.get("action") or "").strip()
    q = (request.args.get("q") or "").strip()

    conn = get_db()
    row = conn.execute("SELECT quantity FROM items WHERE id=?", (item_id,)).fetchone()
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
        raw = (request.form.get("value") or "0").strip()
        try:
            v = int(raw)
            qty = max(0, v)
        except:
            pass

    conn.execute("UPDATE items SET quantity=? WHERE id=?", (qty, item_id))
    conn.commit()
    conn.close()

    return redirect(url_for("index", q=q))


@app.route("/delete/<int:item_id>", methods=["POST"])
def delete_item(item_id):
    q = (request.args.get("q") or "").strip()

    conn = get_db()
    row = conn.execute("SELECT image_filename FROM items WHERE id=?", (item_id,)).fetchone()
    conn.execute("DELETE FROM items WHERE id=?", (item_id,))
    conn.commit()
    conn.close()

    if row and row["image_filename"]:
        path = os.path.join(UPLOAD_FOLDER, row["image_filename"])
        if os.path.exists(path):
            try:
                os.remove(path)
            except:
                pass

    flash("اتمسح ✅", "ok")
    return redirect(url_for("index", q=q))


def load_font(size: int):
    candidates = [
        os.path.join(APP_DIR, "arial.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size=size)
            except:
                continue
    return ImageFont.load_default()


@app.route("/export.png", methods=["GET"])
def export_png():
    f = build_filters_from_args(request.args)
    q = f["q"]

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

    W = 1200
    row_h = 150
    header_h = 140
    padding = 20
    thumb_size = (120, 120)

    H = header_h + max(1, len(items)) * row_h + padding * 2
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)

    title_font = load_font(42)
    header_font = load_font(26)
    text_font = load_font(24)
    small_font = load_font(18)

    draw.text((padding, 20), "تقرير الجرد", fill="black", font=title_font)
    draw.text((padding, 75), datetime.now().strftime("%Y-%m-%d %H:%M"), fill="gray", font=small_font)

    if q:
        draw.text((padding, 105), f"بحث: {q}", fill="gray", font=small_font)

    y = header_h - 10
    draw.line((padding, y, W - padding, y), fill=(0, 0, 0), width=2)

    col_section = 20
    col_model = 200
    col_name = 380
    col_code = 720
    col_qty = 910
    col_img = 1020

    y = header_h + 10
    draw.text((col_section, y), "القسم", fill="black", font=header_font)
    draw.text((col_model, y), "الموديل", fill="black", font=header_font)
    draw.text((col_name, y), "اسم الصنف", fill="black", font=header_font)
    draw.text((col_code, y), "الكود", fill="black", font=header_font)
    draw.text((col_qty, y), "الكمية", fill="black", font=header_font)
    draw.text((col_img, y), "صورة", fill="black", font=header_font)

    y += 40
    draw.line((padding, y, W - padding, y), fill=(0, 0, 0), width=2)
    start_y = y + 15

    if not items:
        draw.text((padding, start_y + 40), "لا يوجد بيانات.", fill="gray", font=header_font)
    else:
        for i, it in enumerate(items):
            row_y = start_y + i * row_h

            if i % 2 == 0:
                draw.rectangle((padding, row_y - 5, W - padding, row_y + row_h - 10), fill=(248, 248, 248))

            draw.text((col_section, row_y + 10), str(it["section"]), fill="black", font=text_font)
            draw.text((col_model, row_y + 10), str(it["model"]), fill="black", font=text_font)
            draw.text((col_name, row_y + 10), str(it["item_name"]), fill="black", font=text_font)
            draw.text((col_code, row_y + 10), str(it["code"]), fill="black", font=text_font)
            draw.text((col_qty, row_y + 10), str(it["quantity"]), fill="black", font=text_font)

            thumb_x = col_img
            thumb_y = row_y + 5
            draw.rectangle((thumb_x, thumb_y, thumb_x + thumb_size[0], thumb_y + thumb_size[1]),
                           outline="black", width=2)

            if it["image_filename"]:
                path = os.path.join(UPLOAD_FOLDER, it["image_filename"])
                if os.path.exists(path):
                    try:
                        im2 = Image.open(path).convert("RGB")
                        im2.thumbnail(thumb_size)
                        ox = thumb_x + (thumb_size[0] - im2.size[0]) // 2
                        oy = thumb_y + (thumb_size[1] - im2.size[1]) // 2
                        img.paste(im2, (ox, oy))
                    except:
                        draw.text((thumb_x + 10, thumb_y + 45), "خطأ", fill="red", font=small_font)

            draw.line((padding, row_y + row_h - 10, W - padding, row_y + row_h - 10),
                      fill=(200, 200, 200), width=2)

    out_path = os.path.join(APP_DIR, "inventory_export.png")
    img.save(out_path, "PNG")
    return send_file(out_path, mimetype="image/png", as_attachment=True, download_name="inventory_export.png")


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)