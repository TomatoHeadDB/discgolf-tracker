import os
import uuid
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    abort,
    send_from_directory,
)
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

from models import db, User, Disc

load_dotenv()

CATEGORIES = ["Putter", "Midrange", "Fairway Driver", "Distance Driver"]
ALLOWED_PHOTO_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_PHOTO_BYTES = 8 * 1024 * 1024  # 8 MB


def create_app():
    app = Flask(__name__)

    basedir = os.path.abspath(os.path.dirname(__file__))
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-change-me")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(basedir, 'instance', 'discgolf.db')}"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = MAX_PHOTO_BYTES

    upload_folder = os.path.join(basedir, "instance", "uploads")
    app.config["UPLOAD_FOLDER"] = upload_folder

    db.init_app(app)

    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    with app.app_context():
        os.makedirs(os.path.join(basedir, "instance"), exist_ok=True)
        os.makedirs(upload_folder, exist_ok=True)
        db.create_all()

    # ---------- Auth ----------

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")

            if not username or not email or not password:
                flash("All fields are required.", "error")
                return render_template("register.html")
            if password != confirm:
                flash("Passwords do not match.", "error")
                return render_template("register.html")
            if User.query.filter(
                (User.username == username) | (User.email == email)
            ).first():
                flash("Username or email already in use.", "error")
                return render_template("register.html")

            user = User(username=username, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash("Welcome! Your account has been created.", "success")
            return redirect(url_for("dashboard"))

        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            identifier = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = User.query.filter(
                (User.username == identifier) | (User.email == identifier.lower())
            ).first()
            if user and user.check_password(password):
                login_user(user)
                next_page = request.args.get("next")
                return redirect(next_page or url_for("dashboard"))
            flash("Invalid username/email or password.", "error")
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("login"))

    # ---------- Pages ----------

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        discs = (
            Disc.query.filter_by(user_id=current_user.id)
            .order_by(Disc.category, Disc.speed.desc())
            .all()
        )
        return render_template("dashboard.html", discs=discs, categories=CATEGORIES)

    @app.route("/discs/new", methods=["GET", "POST"])
    @login_required
    def new_disc():
        if request.method == "POST":
            disc = _disc_from_form(Disc(user_id=current_user.id))
            _handle_photo_upload(disc)
            db.session.add(disc)
            db.session.commit()
            flash(f'Added "{disc.name}" to your bag.', "success")
            return redirect(url_for("dashboard"))
        return render_template(
            "disc_form.html", disc=None, categories=CATEGORIES, action="Add"
        )

    @app.route("/discs/<int:disc_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_disc(disc_id):
        disc = _get_owned_disc(disc_id)
        if request.method == "POST":
            _disc_from_form(disc)
            if request.form.get("remove_photo") == "on":
                _delete_photo_file(disc.photo_filename)
                disc.photo_filename = None
            _handle_photo_upload(disc)
            db.session.commit()
            flash(f'Updated "{disc.name}".', "success")
            return redirect(url_for("dashboard"))
        return render_template(
            "disc_form.html", disc=disc, categories=CATEGORIES, action="Save"
        )

    @app.route("/discs/<int:disc_id>/delete", methods=["POST"])
    @login_required
    def delete_disc(disc_id):
        disc = _get_owned_disc(disc_id)
        _delete_photo_file(disc.photo_filename)
        db.session.delete(disc)
        db.session.commit()
        flash(f'Removed "{disc.name}".', "success")
        return redirect(url_for("dashboard"))

    @app.route("/discs/<int:disc_id>/photo")
    @login_required
    def disc_photo(disc_id):
        disc = _get_owned_disc(disc_id)
        if not disc.photo_filename:
            abort(404)
        return send_from_directory(app.config["UPLOAD_FOLDER"], disc.photo_filename)

    @app.route("/discs/<int:disc_id>/flight")
    @login_required
    def flight_view(disc_id):
        disc = _get_owned_disc(disc_id)
        return render_template("flight.html", disc=disc)

    # ---------- JSON API (used by the flight-path canvas + multi-disc compare) ----------

    @app.route("/api/discs")
    @login_required
    def api_discs():
        discs = Disc.query.filter_by(user_id=current_user.id).all()
        return jsonify([d.to_dict() for d in discs])

    @app.route("/api/discs/<int:disc_id>")
    @login_required
    def api_disc(disc_id):
        disc = _get_owned_disc(disc_id)
        return jsonify(disc.to_dict())

    # ---------- helpers ----------

    def _get_owned_disc(disc_id):
        disc = db.session.get(Disc, disc_id)
        if disc is None or disc.user_id != current_user.id:
            abort(404)
        return disc

    def _allowed_photo(filename):
        return (
            "." in filename
            and filename.rsplit(".", 1)[1].lower() in ALLOWED_PHOTO_EXTENSIONS
        )

    def _delete_photo_file(filename):
        if not filename:
            return
        path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        if os.path.exists(path):
            os.remove(path)

    def _handle_photo_upload(disc):
        file = request.files.get("photo")
        if not file or not file.filename:
            return
        if not _allowed_photo(file.filename):
            flash(
                "Photo not saved: use a JPG, PNG, or WEBP image.", "error"
            )
            return
        ext = secure_filename(file.filename).rsplit(".", 1)[1].lower()
        new_filename = f"{uuid.uuid4().hex}.{ext}"
        file.save(os.path.join(app.config["UPLOAD_FOLDER"], new_filename))
        # Replacing an existing photo — clean up the old file.
        _delete_photo_file(disc.photo_filename)
        disc.photo_filename = new_filename

    def _disc_from_form(disc: Disc) -> Disc:
        def f(field, default=0.0):
            try:
                return float(request.form.get(field, default))
            except (TypeError, ValueError):
                return default

        disc.name = request.form.get("name", "").strip() or "Unnamed Disc"
        disc.manufacturer = request.form.get("manufacturer", "").strip() or None
        disc.category = request.form.get("category") or "Midrange"
        disc.speed = f("speed", 5.0)
        disc.glide = f("glide", 4.0)
        disc.turn = f("turn", 0.0)
        disc.fade = f("fade", 2.0)
        disc.plastic = request.form.get("plastic", "").strip() or None
        disc.color = request.form.get("color") or "#3b82f6"
        weight = request.form.get("weight_grams", "").strip()
        disc.weight_grams = int(weight) if weight.isdigit() else None
        disc.condition = request.form.get("condition") or None
        disc.in_bag = request.form.get("in_bag") == "on"
        disc.notes = request.form.get("notes", "").strip() or None
        return disc

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
