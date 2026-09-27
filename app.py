import os
import uuid
import csv
import io
import re
import json
from datetime import datetime
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

from models import db, User, Disc, Round

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

    @app.route("/discs/<int:disc_id>", methods=["GET", "POST"])
    @login_required
    def view_disc(disc_id):
        disc = _get_owned_disc(disc_id)
        if request.method == "POST":
            disc.notes = request.form.get("notes", "").strip() or None
            db.session.commit()
            flash("Notes updated.", "success")
            return redirect(url_for("view_disc", disc_id=disc.id))
        return render_template("disc_view.html", disc=disc)

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

    # ---------- Rounds (imported UDisc scorecards) ----------

    @app.route("/rounds")
    @login_required
    def rounds():
        user_rounds = (
            Round.query.filter_by(user_id=current_user.id)
            .order_by(Round.played_at.desc())
            .all()
        )
        stats = _compute_round_stats(user_rounds)
        return render_template(
            "rounds.html",
            rounds=user_rounds,
            stats=stats,
            rounds_json=json.dumps([r.to_dict() for r in user_rounds]),
        )

    @app.route("/rounds/import", methods=["GET", "POST"])
    @login_required
    def import_rounds():
        if request.method == "POST":
            display_name = request.form.get("display_name", "").strip()
            file = request.files.get("csv_file")

            if not display_name:
                flash("Enter the name that appears on your UDisc scorecards.", "error")
                return render_template("rounds_import.html")
            if not file or not file.filename:
                flash("Choose a CSV file exported from UDisc.", "error")
                return render_template("rounds_import.html", display_name=display_name)
            if not file.filename.lower().endswith(".csv"):
                flash("That doesn't look like a CSV file.", "error")
                return render_template("rounds_import.html", display_name=display_name)

            current_user.udisc_display_name = display_name
            db.session.commit()

            try:
                added, skipped_dupe, skipped_bad = _import_udisc_csv(file, display_name)
            except Exception:
                flash(
                    "Couldn't read that file — make sure it's an unmodified "
                    "export from UDisc (More → Scorecards → Export to CSV).",
                    "error",
                )
                return render_template("rounds_import.html", display_name=display_name)

            db.session.commit()

            if added:
                flash(
                    f"Imported {added} round(s)."
                    + (f" Skipped {skipped_dupe} already-imported round(s)." if skipped_dupe else "")
                    + (f" Skipped {skipped_bad} row(s) that couldn't be read." if skipped_bad else ""),
                    "success",
                )
            else:
                flash(
                    f'No new rounds found for "{display_name}". '
                    "Double-check the name matches exactly what shows on your UDisc scorecards.",
                    "error",
                )
            return redirect(url_for("rounds"))

        return render_template(
            "rounds_import.html", display_name=current_user.udisc_display_name
        )

    @app.route("/rounds/<int:round_id>/delete", methods=["POST"])
    @login_required
    def delete_round(round_id):
        r = db.session.get(Round, round_id)
        if r is None or r.user_id != current_user.id:
            abort(404)
        db.session.delete(r)
        db.session.commit()
        flash("Round deleted.", "success")
        return redirect(url_for("rounds"))

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

    def _parse_udisc_date(raw):
        raw = (raw or "").strip()
        if not raw:
            return None
        # UDisc's date field looks like "2024-03-15 1014" (date + time with
        # no separator). We only care about the date portion for stats/sorting.
        date_part = raw.split(" ")[0]
        try:
            return datetime.strptime(date_part, "%Y-%m-%d").date()
        except ValueError:
            return None

    def _import_udisc_csv(file_storage, display_name):
        raw = file_storage.read().decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(raw))

        if not reader.fieldnames:
            raise ValueError("Empty or unreadable CSV")

        hole_columns = sorted(
            (f for f in reader.fieldnames if re.match(r"^Hole\d+$", f)),
            key=lambda f: int(re.match(r"^Hole(\d+)$", f).group(1)),
        )

        added = 0
        skipped_dupe = 0
        skipped_bad = 0

        # Existing rounds for this user, to skip re-importing the same round
        # on a second upload of an overlapping export.
        existing = {
            (r.course_name, r.layout_name, r.played_at, r.total_score)
            for r in Round.query.filter_by(user_id=current_user.id).all()
        }

        for row in reader:
            player = (row.get("PlayerName") or "").strip()
            if player.lower() != display_name.lower():
                continue  # not this user's row (includes the "Par" row)

            course = (row.get("CourseName") or "").strip()
            layout = (row.get("LayoutName") or "").strip() or None
            played_at = _parse_udisc_date(row.get("Date"))
            total_raw = (row.get("Total") or "").strip()

            if not course or played_at is None or not total_raw:
                skipped_bad += 1
                continue
            try:
                total_score = int(total_raw)
            except ValueError:
                skipped_bad += 1
                continue

            relative_raw = (row.get("+/-") or "").strip()
            relative_score = None
            if relative_raw not in ("", None):
                try:
                    relative_score = int(relative_raw)
                except ValueError:
                    relative_score = None

            holes = []
            for col in hole_columns:
                val = (row.get(col) or "").strip()
                if val == "":
                    continue
                try:
                    holes.append(int(val))
                except ValueError:
                    pass

            key = (course, layout, played_at, total_score)
            if key in existing:
                skipped_dupe += 1
                continue
            existing.add(key)

            db.session.add(
                Round(
                    user_id=current_user.id,
                    course_name=course,
                    layout_name=layout,
                    played_at=played_at,
                    total_score=total_score,
                    relative_score=relative_score,
                    hole_scores=json.dumps(holes) if holes else None,
                )
            )
            added += 1

        return added, skipped_dupe, skipped_bad

    def _compute_round_stats(user_rounds):
        if not user_rounds:
            return {"avg_by_course": [], "total_rounds": 0, "best_relative": None}

        by_course = {}
        for r in user_rounds:
            by_course.setdefault(r.course_name, []).append(r)

        avg_by_course = sorted(
            (
                {
                    "course_name": course,
                    "round_count": len(rs),
                    "avg_score": round(sum(x.total_score for x in rs) / len(rs), 1),
                }
                for course, rs in by_course.items()
            ),
            key=lambda x: x["course_name"],
        )

        relative_scores = [r.relative_score for r in user_rounds if r.relative_score is not None]
        best_relative = min(relative_scores) if relative_scores else None

        return {
            "avg_by_course": avg_by_course,
            "total_rounds": len(user_rounds),
            "best_relative": best_relative,
        }

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
