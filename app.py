import json
import os
import secrets
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from flask import (
    Flask, abort, flash, g, redirect, render_template, request, send_from_directory,
    session, url_for
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, or_
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_UPLOAD_DIR = BASE_DIR / "static" / "uploads"
load_dotenv(BASE_DIR / ".env")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
app.config["UPLOAD_FOLDER"] = os.environ.get("UPLOAD_DIR", str(DEFAULT_UPLOAD_DIR))
Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

raw_database_url = os.environ.get("DATABASE_URL", "").strip()
if raw_database_url:
    # Render and some other hosts still supply postgres:// URLs; SQLAlchemy 2 expects a driver.
    if raw_database_url.startswith("postgres://"):
        raw_database_url = raw_database_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif raw_database_url.startswith("postgresql://"):
        raw_database_url = raw_database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = raw_database_url
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{INSTANCE_DIR / 'bvgartts.db'}"

# SQLite needs this option when Flask serves multiple requests locally.
if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:"):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"connect_args": {"check_same_thread": False}}

db = SQLAlchemy(app)
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
CATEGORIES = ["Original", "Print", "Digital Art", "Vintage"]
ORDER_STATUSES = ["Pending confirmation", "Awaiting payment", "Paid", "Processing", "Shipped", "Completed", "Cancelled"]


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(10), nullable=False, default="user")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    cart_items = db.relationship("CartItem", backref="user", cascade="all, delete-orphan")
    orders = db.relationship("Order", backref="user", order_by="Order.created_at.desc()")


class Artwork(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    artist = db.Column(db.String(100), nullable=False, default="BVGArtts Artist")
    description = db.Column(db.Text, nullable=False, default="")
    price = db.Column(db.Integer, nullable=False)  # Naira, stored as whole naira
    category = db.Column(db.String(30), nullable=False, default="Original")
    image_url = db.Column(db.String(700), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class CartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    artwork_id = db.Column(db.Integer, db.ForeignKey("artwork.id", ondelete="CASCADE"), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    artwork = db.relationship("Artwork")
    __table_args__ = (db.UniqueConstraint("user_id", "artwork_id", name="uq_cart_user_artwork"),)


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    customer_name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(30), nullable=False)
    address = db.Column(db.Text, nullable=False)
    note = db.Column(db.Text, nullable=False, default="")
    total = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(40), nullable=False, default="Pending confirmation")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    items = db.relationship("OrderItem", backref="order", cascade="all, delete-orphan")


class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id", ondelete="CASCADE"), nullable=False, index=True)
    artwork_id = db.Column(db.Integer, nullable=True)  # Snapshot survives if artwork is later removed.
    title = db.Column(db.String(120), nullable=False)
    price = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)


def format_naira(value):
    return "₦" + f"{int(value):,}"


app.jinja_env.filters["naira"] = format_naira


@app.before_request
def security_and_current_user():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not sent or not expected or not secrets.compare_digest(sent, expected):
            abort(400, description="The page security token expired. Please go back, refresh, and try again.")
    g.user = None
    user_id = session.get("user_id")
    if user_id:
        g.user = db.session.get(User, user_id)
        if g.user is None:
            session.clear()


@app.context_processor
def inject_shared_template_values():
    cart_count = 0
    if getattr(g, "user", None):
        cart_count = sum(item.quantity for item in g.user.cart_items)
    return {
        "current_user": getattr(g, "user", None),
        "csrf_token": session.get("csrf_token", ""),
        "cart_count": cart_count,
        "categories": CATEGORIES,
        "order_statuses": ORDER_STATUSES,
    }


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            flash("Create an account or log in to continue.", "info")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            flash("Please log in with an administrator account.", "info")
            return redirect(url_for("login", next=request.path))
        if g.user.role != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def valid_image_url(value):
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("/uploads/"):
        return value
    parsed = urlparse(value)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return value[:700]
    raise ValueError("Use a complete https:// image link or upload an image file.")


def save_uploaded_image(file_storage):
    if not file_storage or not file_storage.filename:
        return ""
    safe_name = secure_filename(file_storage.filename)
    ext = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError("Image must be JPG, JPEG, PNG, WEBP or GIF.")
    unique_name = f"{uuid.uuid4().hex}.{ext}"
    destination = Path(app.config["UPLOAD_FOLDER"]) / unique_name
    file_storage.save(destination)
    return f"/uploads/{unique_name}"


def get_artwork_image(form, existing=""):
    uploaded = request.files.get("image_file")
    if uploaded and uploaded.filename:
        return save_uploaded_image(uploaded)
    submitted_url = form.get("image_url", "").strip()
    if submitted_url:
        return valid_image_url(submitted_url)
    return existing


def delete_uploaded_image(image_url):
    if image_url and image_url.startswith("/uploads/"):
        filename = image_url.rsplit("/", 1)[-1]
        try:
            (Path(app.config["UPLOAD_FOLDER"]) / filename).unlink(missing_ok=True)
        except OSError:
            pass


def seed_admin_accounts():
    """Create admins from ADMIN_USERS_JSON on first deployment; never overwrite existing accounts."""
    raw = os.environ.get("ADMIN_USERS_JSON", "").strip()
    if not raw:
        return
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        app.logger.error("ADMIN_USERS_JSON must be a JSON list of username/password objects.")
        return
    if not isinstance(entries, list):
        app.logger.error("ADMIN_USERS_JSON must be a JSON list.")
        return
    changed = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        username = str(entry.get("username", "")).strip()
        password = str(entry.get("password", ""))
        if not username or not password or len(username) > 40:
            continue
        user = User.query.filter(func.lower(User.username) == username.lower()).first()
        if not user:
            db.session.add(User(username=username, password_hash=generate_password_hash(password), role="admin"))
            changed = True
        elif user.role == "admin":
            # Existing credentials are intentionally not reset by an environment change.
            continue
    if changed:
        db.session.commit()


def seed_demo_artworks():
    if Artwork.query.count() > 0:
        return
    sample = [
        ("Blue Hour", "Mira Okafor", "A serene blue-toned statement piece for a living room or studio.", 85000, "Original", "https://images.unsplash.com/photo-1579783902614-a3fb3927b6a5?auto=format&fit=crop&w=900&q=85"),
        ("Colour Study No. 4", "Studio Nia", "A lively contemporary print with bold colour and shape.", 24000, "Print", "https://images.unsplash.com/photo-1541961017774-22349e4a1262?auto=format&fit=crop&w=900&q=85"),
        ("Soft Geometry", "K. Adeyemi", "A digital artwork for modern spaces and creative corners.", 18000, "Digital Art", "https://images.unsplash.com/photo-1549490349-8643362247b5?auto=format&fit=crop&w=900&q=85"),
        ("Quiet Garden", "Lola A.", "Layered colours inspired by a quiet afternoon in the garden.", 112000, "Original", "https://images.unsplash.com/photo-1577083552431-6e5fd01aa342?auto=format&fit=crop&w=900&q=85"),
        ("Golden Afternoon", "The Print Room", "A warm art print suited to a bedroom, office or reading nook.", 16500, "Print", "https://images.unsplash.com/photo-1578301978693-85fa9c0320b9?auto=format&fit=crop&w=900&q=85"),
        ("The Old Atelier", "BVGArtts Curated", "A vintage-inspired art find selected for collectors.", 97000, "Vintage", "https://images.unsplash.com/photo-1577083288073-40892c0860a4?auto=format&fit=crop&w=900&q=85"),
    ]
    for title, artist, description, price, category, image_url in sample:
        db.session.add(Artwork(title=title, artist=artist, description=description, price=price, category=category, image_url=image_url))
    db.session.commit()


@app.route("/")
def home():
    query = request.args.get("q", "").strip()
    category = request.args.get("category", "All")
    if category not in ["All", *CATEGORIES]:
        category = "All"
    artwork_query = Artwork.query
    if category != "All":
        artwork_query = artwork_query.filter_by(category=category)
    if query:
        term = f"%{query}%"
        artwork_query = artwork_query.filter(or_(Artwork.title.ilike(term), Artwork.artist.ilike(term), Artwork.description.ilike(term)))
    artworks = artwork_query.order_by(Artwork.created_at.desc()).all()
    return render_template("index.html", artworks=artworks, search_query=query, selected_category=category)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if g.user:
        return redirect(url_for("home"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        normalized = username.lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not (3 <= len(username) <= 24) or not all(c.isalnum() or c in "_-" for c in username):
            flash("Username must be 3–24 characters and use only letters, numbers, _ or -.", "error")
        elif len(password) < 8:
            flash("Choose a password with at least 8 characters.", "error")
        elif password != confirm:
            flash("The passwords do not match.", "error")
        elif User.query.filter(func.lower(User.username) == normalized).first():
            flash("That username is already taken. Try another one.", "error")
        else:
            account = User(username=normalized, password_hash=generate_password_hash(password), role="user")
            db.session.add(account)
            db.session.commit()
            session.clear()
            session["user_id"] = account.id
            session["csrf_token"] = secrets.token_urlsafe(32)
            flash("Your BVGArtts account is ready. Welcome!", "success")
            return redirect(url_for("home"))
    return render_template("auth.html", mode="signup")


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("admin_dashboard" if g.user.role == "admin" else "home"))
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter(func.lower(User.username) == username).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Username or password is incorrect.", "error")
        else:
            session.clear()
            session["user_id"] = user.id
            session["csrf_token"] = secrets.token_urlsafe(32)
            flash("Welcome back!", "success")
            next_url = request.args.get("next", "")
            # Only allow a local relative path for post-login redirects.
            if next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for("admin_dashboard" if user.role == "admin" else "home"))
    return render_template("auth.html", mode="login")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))


@app.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        old_password = request.form.get("old_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not check_password_hash(g.user.password_hash, old_password):
            flash("Your current password was not correct.", "error")
        elif len(new_password) < 10:
            flash("Choose a new password with at least 10 characters.", "error")
        elif new_password != confirm_password:
            flash("The new passwords do not match.", "error")
        elif new_password == old_password:
            flash("Your new password must be different from the current one.", "error")
        else:
            g.user.password_hash = generate_password_hash(new_password)
            db.session.commit()
            flash("Password updated successfully.", "success")
            return redirect(url_for("admin_dashboard" if g.user.role == "admin" else "home"))
    return render_template("change_password.html")


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/cart")
@login_required
def cart():
    items = CartItem.query.filter_by(user_id=g.user.id).all()
    total = sum(item.artwork.price * item.quantity for item in items if item.artwork)
    return render_template("cart.html", items=items, total=total)


@app.route("/cart/add/<int:artwork_id>", methods=["POST"])
@login_required
def add_to_cart(artwork_id):
    artwork = db.session.get(Artwork, artwork_id)
    if not artwork:
        abort(404)
    item = CartItem.query.filter_by(user_id=g.user.id, artwork_id=artwork.id).first()
    if item:
        item.quantity = min(item.quantity + 1, 20)
    else:
        db.session.add(CartItem(user_id=g.user.id, artwork_id=artwork.id, quantity=1))
    db.session.commit()
    flash(f"{artwork.title} added to your bag.", "success")
    return_to = request.form.get("return_to", "")
    return redirect(return_to if return_to.startswith("/") and not return_to.startswith("//") else url_for("home"))


@app.route("/cart/update/<int:item_id>", methods=["POST"])
@login_required
def update_cart(item_id):
    item = CartItem.query.filter_by(id=item_id, user_id=g.user.id).first_or_404()
    try:
        quantity = int(request.form.get("quantity", "1"))
    except ValueError:
        quantity = 1
    if quantity < 1:
        db.session.delete(item)
    else:
        item.quantity = min(quantity, 20)
    db.session.commit()
    return redirect(url_for("cart"))


@app.route("/cart/remove/<int:item_id>", methods=["POST"])
@login_required
def remove_from_cart(item_id):
    item = CartItem.query.filter_by(id=item_id, user_id=g.user.id).first_or_404()
    db.session.delete(item)
    db.session.commit()
    flash("Item removed from your bag.", "info")
    return redirect(url_for("cart"))


@app.route("/checkout", methods=["GET", "POST"])
@login_required
def checkout():
    items = CartItem.query.filter_by(user_id=g.user.id).all()
    items = [item for item in items if item.artwork]
    if not items:
        flash("Your bag is empty. Add an artwork before checking out.", "info")
        return redirect(url_for("home"))
    total = sum(item.artwork.price * item.quantity for item in items)
    if request.method == "POST":
        customer_name = request.form.get("customer_name", "").strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        note = request.form.get("note", "").strip()
        if len(customer_name) < 2 or len(phone) < 7 or len(address) < 8:
            flash("Enter your name, a valid phone number and a delivery address.", "error")
            return render_template("checkout.html", items=items, total=total)
        order = Order(user_id=g.user.id, customer_name=customer_name[:100], phone=phone[:30], address=address[:1000], note=note[:1000], total=total, status="Pending confirmation")
        db.session.add(order)
        db.session.flush()
        for item in items:
            db.session.add(OrderItem(order_id=order.id, artwork_id=item.artwork.id, title=item.artwork.title, price=item.artwork.price, quantity=item.quantity))
            db.session.delete(item)
        db.session.commit()
        flash(f"Order #{order.id} has been sent to BVGArtts for confirmation. Payment has not been taken.", "success")
        return redirect(url_for("my_orders"))
    return render_template("checkout.html", items=items, total=total)


@app.route("/my-orders")
@login_required
def my_orders():
    orders = Order.query.filter_by(user_id=g.user.id).order_by(Order.created_at.desc()).all()
    return render_template("orders.html", orders=orders, admin_view=False)


@app.route("/admin")
@admin_required
def admin_dashboard():
    artworks = Artwork.query.order_by(Artwork.created_at.desc()).all()
    orders = Order.query.order_by(Order.created_at.desc()).limit(100).all()
    users_count = User.query.filter_by(role="user").count()
    return render_template("admin.html", artworks=artworks, orders=orders, users_count=users_count)


@app.route("/admin/artworks/new", methods=["POST"])
@admin_required
def create_artwork():
    title = request.form.get("title", "").strip()
    artist = request.form.get("artist", "").strip() or "BVGArtts Artist"
    description = request.form.get("description", "").strip()
    category = request.form.get("category", "Original")
    try:
        price = int(request.form.get("price", "0"))
        image_url = get_artwork_image(request.form)
        if len(title) < 2 or len(title) > 120:
            raise ValueError("Artwork title must be between 2 and 120 characters.")
        if price < 1 or price > 1_000_000_000:
            raise ValueError("Enter a valid price in naira.")
        if category not in CATEGORIES:
            raise ValueError("Choose a valid category.")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin_dashboard") + "#add-artwork")
    artwork = Artwork(title=title, artist=artist[:100], description=description[:2500], price=price, category=category, image_url=image_url)
    db.session.add(artwork)
    db.session.commit()
    flash("Artwork published on BVGArtts.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/artworks/<int:artwork_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_artwork(artwork_id):
    artwork = db.session.get(Artwork, artwork_id)
    if not artwork:
        abort(404)
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        artist = request.form.get("artist", "").strip() or "BVGArtts Artist"
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "Original")
        try:
            price = int(request.form.get("price", "0"))
            new_image = get_artwork_image(request.form, artwork.image_url)
            if len(title) < 2 or len(title) > 120:
                raise ValueError("Artwork title must be between 2 and 120 characters.")
            if price < 1 or price > 1_000_000_000:
                raise ValueError("Enter a valid price in naira.")
            if category not in CATEGORIES:
                raise ValueError("Choose a valid category.")
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("edit_artwork", artwork_id=artwork.id))
        if new_image != artwork.image_url:
            delete_uploaded_image(artwork.image_url)
        artwork.title, artwork.artist, artwork.description = title, artist[:100], description[:2500]
        artwork.price, artwork.category, artwork.image_url = price, category, new_image
        db.session.commit()
        flash("Artwork updated.", "success")
        return redirect(url_for("admin_dashboard"))
    return render_template("edit_artwork.html", artwork=artwork)


@app.route("/admin/artworks/<int:artwork_id>/delete", methods=["POST"])
@admin_required
def delete_artwork(artwork_id):
    artwork = db.session.get(Artwork, artwork_id)
    if not artwork:
        abort(404)
    image_url = artwork.image_url
    CartItem.query.filter_by(artwork_id=artwork.id).delete(synchronize_session=False)
    db.session.delete(artwork)
    db.session.commit()
    delete_uploaded_image(image_url)
    flash("Artwork removed from the gallery.", "info")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/orders/<int:order_id>/status", methods=["POST"])
@admin_required
def update_order_status(order_id):
    order = db.session.get(Order, order_id)
    if not order:
        abort(404)
    status = request.form.get("status", "")
    if status not in ORDER_STATUSES:
        abort(400, description="Invalid order status.")
    order.status = status
    db.session.commit()
    flash(f"Order #{order.id} status updated to {status}.", "success")
    return redirect(url_for("admin_dashboard") + "#orders")


@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html", code=403, heading="Admin access only", message="This area is for BVGArtts administrators."), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, heading="We couldn't find that page", message="The artwork or page may have moved."), 404


@app.errorhandler(400)
def bad_request(error):
    description = getattr(error, "description", "That request could not be processed.")
    return render_template("error.html", code=400, heading="Let's try that again", message=description), 400


with app.app_context():
    db.create_all()
    seed_admin_accounts()
    seed_demo_artworks()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=os.environ.get("APP_DEBUG", "false").lower() == "true")
