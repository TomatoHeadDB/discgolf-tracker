import os
import uuid
import csv
import io
import re
import json
import hmac
import secrets
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
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from PIL import Image, ImageOps
from sqlalchemy.exc import IntegrityError

from models import (
    db,
    User,
    Disc,
    Round,
    RoundPlayerScore,
    ThrowMeasurement,
    Connection,
    Group,
    GroupMember,
)

load_dotenv()

CATEGORIES = ["Putter", "Midrange", "Fairway Driver", "Distance Driver"]
ALLOWED_PHOTO_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_PHOTO_BYTES = 8 * 1024 * 1024  # 8 MB
AVATAR_SIZE = 256  # px, square
BANNER_SIZE = (1200, 300)  # px, 4:1 group banner
MAX_OWNED_GROUPS = 5

# Refuse absurdly large images outright (Pillow raises at 2x this) so one
# upload can't exhaust the small container's memory.
Image.MAX_IMAGE_PIXELS = 25_000_000

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}


def normalize_county(raw):
    """Trim and drop a trailing 'County' so 'Hillsborough County' and
    'hillsborough' match each other. Returns None if empty."""
    raw = re.sub(r"\s+county$", "", (raw or "").strip(), flags=re.I).strip()
    return raw[:120] or None


def create_app():
    app = Flask(__name__)
    # Trust the reverse proxy's headers (X-Forwarded-For, X-Forwarded-Proto)
    # so Flask knows a request arrived over HTTPS even though the proxy
    # talks to us over plain HTTP internally. Needed for secure cookies
    # and correct https:// URLs to work when running behind NPM.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    basedir = os.path.abspath(os.path.dirname(__file__))
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-change-me")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(basedir, 'instance', 'discgolf.db')}"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = MAX_PHOTO_BYTES

    # Only mark cookies "secure" (HTTPS-only) once actually running behind
    # HTTPS — set FORCE_HTTPS=true in .env once your reverse proxy is live.
    # Leaving this off breaks nothing locally over plain http://.
    app.config["SESSION_COOKIE_SECURE"] = (
        os.environ.get("FORCE_HTTPS", "false").lower() == "true"
    )
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    upload_folder = os.path.join(basedir, "instance", "uploads")
    app.config["UPLOAD_FOLDER"] = upload_folder

    db.init_app(app)

    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.init_app(app)

    limiter = Limiter(get_remote_address, app=app, default_limits=[])

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    @app.context_processor
    def inject_globals():
        return {"US_STATES": US_STATES}

    with app.app_context():
        os.makedirs(os.path.join(basedir, "instance"), exist_ok=True)
        os.makedirs(upload_folder, exist_ok=True)
        db.create_all()

    # ---------- Auth ----------

    @app.route("/register", methods=["GET", "POST"])
    @limiter.limit("10 per hour", methods=["POST"])
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
    @limiter.limit("8 per minute", methods=["POST"])
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

    @app.route("/account", methods=["GET", "POST"])
    @login_required
    def account():
        if request.method == "POST":
            action = request.form.get("action")

            if action == "update_profile":
                current_user.udisc_display_name = (
                    request.form.get("udisc_display_name", "").strip() or None
                )
                current_user.bag_public = request.form.get("bag_public") == "on"

                state = request.form.get("state", "").strip().upper()
                current_user.state = state if state in US_STATES else None
                current_user.county = (
                    normalize_county(request.form.get("county"))
                    if current_user.state
                    else None
                )
                # Can't advertise a region you haven't set.
                current_user.advertise_region = (
                    request.form.get("advertise_region") == "on"
                    and current_user.state is not None
                )

                if request.form.get("remove_profile_photo") == "on":
                    _delete_photo_file(current_user.profile_photo)
                    current_user.profile_photo = None
                _handle_avatar_upload(current_user)

                db.session.commit()
                flash("Settings updated.", "success")

            elif action == "change_password":
                current_pw = request.form.get("current_password", "")
                new_pw = request.form.get("new_password", "")
                confirm_pw = request.form.get("confirm_password", "")

                if not current_user.check_password(current_pw):
                    flash("Current password is incorrect.", "error")
                elif len(new_pw) < 6:
                    flash("New password must be at least 6 characters.", "error")
                elif new_pw != confirm_pw:
                    flash("New passwords do not match.", "error")
                else:
                    current_user.set_password(new_pw)
                    db.session.commit()
                    flash("Password changed.", "success")

            return redirect(url_for("account"))

        return render_template("account.html")

    @app.route("/avatar/<int:user_id>")
    @login_required
    def avatar(user_id):
        user = db.session.get(User, user_id)
        if user is None or not user.profile_photo:
            abort(404)
        # Filenames are random per upload and templates add ?v=<filename>,
        # so it's safe to let browsers cache these for a long time.
        return send_from_directory(
            app.config["UPLOAD_FOLDER"], user.profile_photo, max_age=60 * 60 * 24 * 30
        )

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
        throws = ThrowMeasurement.query.filter_by(disc_id=disc.id, user_id=current_user.id).all()
        avg_distance = (
            round(sum(t.distance_feet for t in throws) / len(throws)) if throws else None
        )
        return render_template(
            "disc_view.html", disc=disc, avg_distance=avg_distance, throw_count=len(throws)
        )

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

    @app.route("/discs/<int:disc_id>/measure", methods=["GET", "POST"])
    @login_required
    def measure_disc(disc_id):
        disc = _get_owned_disc(disc_id)
        if request.method == "POST":
            try:
                distance = float(request.form.get("distance_feet", ""))
            except ValueError:
                flash("Couldn't read that measurement — try again.", "error")
                return redirect(url_for("measure_disc", disc_id=disc.id))
            if distance <= 0 or distance > 2000:
                flash("That distance doesn't look right — try again.", "error")
                return redirect(url_for("measure_disc", disc_id=disc.id))
            db.session.add(
                ThrowMeasurement(
                    user_id=current_user.id, disc_id=disc.id, distance_feet=distance
                )
            )
            db.session.commit()
            flash(f"Saved throw: {round(distance)} ft.", "success")
            return redirect(url_for("measure_disc", disc_id=disc.id))

        throws = (
            ThrowMeasurement.query.filter_by(disc_id=disc.id, user_id=current_user.id)
            .order_by(ThrowMeasurement.recorded_at.desc())
            .all()
        )
        avg_distance = (
            round(sum(t.distance_feet for t in throws) / len(throws)) if throws else None
        )
        return render_template(
            "measure.html", disc=disc, throws=throws, avg_distance=avg_distance
        )

    @app.route("/discs/<int:disc_id>/measure/<int:throw_id>/delete", methods=["POST"])
    @login_required
    def delete_throw(disc_id, throw_id):
        disc = _get_owned_disc(disc_id)
        t = db.session.get(ThrowMeasurement, throw_id)
        if t is None or t.user_id != current_user.id or t.disc_id != disc.id:
            abort(404)
        db.session.delete(t)
        db.session.commit()
        return redirect(url_for("measure_disc", disc_id=disc.id))

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

    @app.route("/rounds/<int:round_id>")
    @login_required
    def round_detail(round_id):
        r = db.session.get(Round, round_id)
        if r is None or r.user_id != current_user.id:
            abort(404)
        players = [
            {
                "name": current_user.udisc_display_name or "You",
                "total": r.total_score,
                "relative": r.relative_score,
                "is_me": True,
            }
        ]
        for ps in r.player_scores:
            players.append(
                {
                    "name": ps.player_name,
                    "total": ps.total_score,
                    "relative": ps.relative_score,
                    "is_me": False,
                }
            )
        players.sort(key=lambda p: p["total"])
        hole_scores = json.loads(r.hole_scores) if r.hole_scores else []
        return render_template(
            "round_detail.html", round=r, players=players, hole_scores=hole_scores
        )

    @app.route("/rounds/course/<path:course_name>")
    @login_required
    def course_detail(course_name):
        course_rounds = (
            Round.query.filter_by(user_id=current_user.id, course_name=course_name)
            .order_by(Round.played_at.desc())
            .all()
        )
        if not course_rounds:
            abort(404)
        scores = [r.total_score for r in course_rounds]
        rel_scores = [r.relative_score for r in course_rounds if r.relative_score is not None]
        summary = {
            "round_count": len(course_rounds),
            "avg_score": round(sum(scores) / len(scores), 1),
            "best_score": min(scores),
            "avg_relative": round(sum(rel_scores) / len(rel_scores), 1) if rel_scores else None,
            "best_relative": min(rel_scores) if rel_scores else None,
        }
        return render_template(
            "course_detail.html",
            course_name=course_name,
            rounds=course_rounds,
            summary=summary,
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

    # ---------- Social ----------

    def _connection_between(user_a_id, user_b_id):
        return Connection.query.filter(
            db.or_(
                db.and_(Connection.requester_id == user_a_id, Connection.recipient_id == user_b_id),
                db.and_(Connection.requester_id == user_b_id, Connection.recipient_id == user_a_id),
            )
        ).first()

    def _accepted_connections(user_id):
        conns = Connection.query.filter(
            Connection.status == "accepted",
            db.or_(Connection.requester_id == user_id, Connection.recipient_id == user_id),
        ).all()
        friends = []
        for c in conns:
            friend = c.recipient if c.requester_id == user_id else c.requester
            friends.append(friend)
        return friends

    @app.route("/social", methods=["GET"])
    @login_required
    def social():
        q = request.args.get("q", "").strip()
        region_state = request.args.get("state", "").strip().upper()
        if region_state not in US_STATES:
            region_state = ""
        region_county = normalize_county(request.args.get("county")) if region_state else None

        search_results = []
        searched = bool(q or region_state)
        if searched:
            query = User.query.filter(User.id != current_user.id)
            if q:
                query = query.filter(User.username.ilike(f"%{q}%"))
            if region_state:
                # Region searches only ever return people who opted in.
                query = query.filter(
                    User.advertise_region.is_(True), User.state == region_state
                )
                if region_county:
                    query = query.filter(
                        db.func.lower(User.county) == region_county.lower()
                    )
            search_results = query.order_by(User.username).limit(50).all()

        pending_incoming = Connection.query.filter_by(
            recipient_id=current_user.id, status="pending"
        ).all()
        pending_outgoing = Connection.query.filter_by(
            requester_id=current_user.id, status="pending"
        ).all()
        friends = _accepted_connections(current_user.id)

        # Combined leaderboards: for every course the current user has
        # logged a round at, rank them against any connected friends who
        # have also logged rounds there.
        my_courses = {r.course_name for r in Round.query.filter_by(user_id=current_user.id).all()}
        friend_ids = [f.id for f in friends]
        leaderboards = []
        for course in sorted(my_courses):
            participants = [current_user] + friends
            rows = []
            for person in participants:
                rel_scores = [
                    r.relative_score
                    for r in Round.query.filter_by(user_id=person.id, course_name=course).all()
                    if r.relative_score is not None
                ]
                if not rel_scores:
                    continue
                rows.append(
                    {
                        "username": person.username,
                        "is_me": person.id == current_user.id,
                        "avg_relative": round(sum(rel_scores) / len(rel_scores), 1),
                        "round_count": len(rel_scores),
                    }
                )
            if len(rows) < 2:
                continue  # no point showing a "leaderboard" of just yourself
            rows.sort(key=lambda x: x["avg_relative"])
            for i, row in enumerate(rows, start=1):
                row["rank"] = i
            leaderboards.append({"course_name": course, "rows": rows})

        return render_template(
            "social.html",
            search_query=q,
            search_state=region_state,
            search_county=region_county or "",
            searched=searched,
            search_results=search_results,
            pending_incoming=pending_incoming,
            pending_outgoing=pending_outgoing,
            friends=friends,
            leaderboards=leaderboards,
        )

    @app.route("/social/connect/<username>", methods=["POST"])
    @login_required
    def send_connection_request(username):
        target = User.query.filter_by(username=username).first()
        if target is None or target.id == current_user.id:
            abort(404)
        if _connection_between(current_user.id, target.id) is not None:
            flash("A connection already exists with that user.", "error")
            return redirect(url_for("social"))
        db.session.add(Connection(requester_id=current_user.id, recipient_id=target.id))
        db.session.commit()
        flash(f"Connection request sent to {target.username}.", "success")
        return redirect(url_for("social"))

    @app.route("/social/respond/<int:connection_id>", methods=["POST"])
    @login_required
    def respond_connection_request(connection_id):
        conn = db.session.get(Connection, connection_id)
        if conn is None or conn.recipient_id != current_user.id:
            abort(404)
        decision = request.form.get("decision")
        if decision == "accept":
            conn.status = "accepted"
            db.session.commit()
            flash("Connection accepted.", "success")
        else:
            db.session.delete(conn)
            db.session.commit()
            flash("Request declined.", "success")
        return redirect(url_for("social"))

    @app.route("/social/disconnect/<int:connection_id>", methods=["POST"])
    @login_required
    def remove_connection(connection_id):
        conn = db.session.get(Connection, connection_id)
        if conn is None or current_user.id not in (conn.requester_id, conn.recipient_id):
            abort(404)
        db.session.delete(conn)
        db.session.commit()
        flash("Connection removed.", "success")
        return redirect(url_for("social"))

    @app.route("/u/<username>")
    @login_required
    def public_profile(username):
        user = User.query.filter_by(username=username).first()
        if user is None:
            abort(404)
        conn = _connection_between(current_user.id, user.id)
        is_connected = conn is not None and conn.status == "accepted"
        pending_from_me = (
            conn is not None and conn.status == "pending" and conn.requester_id == current_user.id
        )
        pending_from_them = (
            conn is not None and conn.status == "pending" and conn.recipient_id == current_user.id
        )
        bag = []
        if user.id == current_user.id or user.bag_public:
            bag = Disc.query.filter_by(user_id=user.id, in_bag=True).all()
        return render_template(
            "public_profile.html",
            profile_user=user,
            bag=bag,
            is_connected=is_connected,
            pending_from_me=pending_from_me,
            pending_from_them=pending_from_them,
            pending_connection=conn,
        )

    # ---------- Groups ----------
    #
    # Roles: "owner" (creator, can do everything), "admin" (only the
    # permissions the owner granted), "member". Membership status is
    # "active" or "pending" (waiting for approval on approval-only groups).
    # Non-members get a 404 on management pages so private groups don't
    # reveal that they exist.

    def _get_group_or_404(group_id):
        group = db.session.get(Group, group_id)
        if group is None:
            abort(404)
        return group

    def _membership(group_id, user_id, active_only=True):
        m = GroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
        if m is None or (active_only and m.status != "active"):
            return None
        return m

    def _can(member, perm):
        """Owner can do everything; admins only what they were granted."""
        if member is None or member.status != "active":
            return False
        if member.role == "owner":
            return True
        if member.role == "admin":
            return bool(getattr(member, f"perm_{perm}", False))
        return False

    def _require_member(group_id):
        group = _get_group_or_404(group_id)
        me = _membership(group.id, current_user.id)
        if me is None:
            abort(404)
        return group, me

    def _code_matches(group, code):
        return bool(code) and hmac.compare_digest(
            code.encode("utf-8"), group.invite_code.encode("utf-8")
        )

    def _region_from_form():
        state = request.form.get("state", "").strip().upper()
        state = state if state in US_STATES else None
        county = normalize_county(request.form.get("county")) if state else None
        advertise = request.form.get("advertise_region") == "on" and state is not None
        return state, county, advertise

    def _valid_group_name(name):
        return 3 <= len(name) <= 60

    def _save_banner(file):
        """Crop/resize an uploaded banner to a wide JPEG. Returns the new
        filename, or None (after flashing why) if nothing usable was sent."""
        if not file or not file.filename:
            return None
        if not _allowed_photo(file.filename):
            flash("Banner not saved: use a JPG, PNG, or WEBP image.", "error")
            return None
        try:
            img = Image.open(file.stream)
            img = ImageOps.exif_transpose(img)
            img = ImageOps.fit(img.convert("RGB"), BANNER_SIZE)
        except Exception:
            flash("Banner not saved: that file couldn't be read as an image.", "error")
            return None
        name = f"banner_{uuid.uuid4().hex}.jpg"
        img.save(os.path.join(app.config["UPLOAD_FOLDER"], name), "JPEG", quality=85)
        return name

    @app.route("/social/groups")
    @login_required
    def groups_home():
        my_rows = (
            GroupMember.query.filter_by(user_id=current_user.id)
            .join(Group, GroupMember.group_id == Group.id)
            .order_by(Group.name)
            .all()
        )
        my_groups = [m for m in my_rows if m.status == "active"]
        my_pending = [m for m in my_rows if m.status == "pending"]
        my_group_ids = {m.group_id for m in my_rows}

        # How many join requests are waiting on groups I can approve for
        waiting = {}
        for m in my_groups:
            if _can(m, "manage_members"):
                n = GroupMember.query.filter_by(group_id=m.group_id, status="pending").count()
                if n:
                    waiting[m.group_id] = n

        q = request.args.get("q", "").strip()
        state = request.args.get("state", "").strip().upper()
        if state not in US_STATES:
            state = ""
        county = normalize_county(request.args.get("county")) if state else None
        searched = bool(q or state)
        default_region = False
        if not searched and current_user.state:
            # Nothing typed yet: suggest groups advertising in my own state.
            state = current_user.state
            searched = True
            default_region = True

        results = []
        if searched:
            query = Group.query.filter(Group.advertise_region.is_(True))
            if q:
                query = query.filter(Group.name.ilike(f"%{q}%"))
            if state:
                query = query.filter(Group.state == state)
            if county:
                query = query.filter(db.func.lower(Group.county) == county.lower())
            results = query.order_by(Group.name).limit(30).all()

        return render_template(
            "groups.html",
            my_groups=my_groups,
            my_pending=my_pending,
            my_group_ids=my_group_ids,
            waiting=waiting,
            search_q=q,
            search_state=state,
            search_county=county or "",
            searched=searched,
            default_region=default_region,
            results=results,
            max_owned=MAX_OWNED_GROUPS,
        )

    @app.route("/social/groups/new", methods=["GET", "POST"])
    @login_required
    @limiter.limit("10 per hour", methods=["POST"])
    def group_new():
        owned = Group.query.filter_by(owner_id=current_user.id).count()
        if request.method == "POST":
            if owned >= MAX_OWNED_GROUPS:
                flash(f"You can own up to {MAX_OWNED_GROUPS} groups.", "error")
                return redirect(url_for("groups_home"))
            name = request.form.get("name", "").strip()
            if not _valid_group_name(name):
                flash("Group name must be 3-60 characters.", "error")
                return render_template("group_form.html", form=request.form)
            join_mode = request.form.get("join_mode")
            if join_mode not in ("open", "approval"):
                join_mode = "open"
            state, county, advertise = _region_from_form()
            group = Group(
                name=name,
                description=request.form.get("description", "").strip()[:1000] or None,
                owner_id=current_user.id,
                join_mode=join_mode,
                state=state,
                county=county,
                advertise_region=advertise,
            )
            db.session.add(group)
            db.session.flush()  # assigns group.id for the membership row
            db.session.add(
                GroupMember(
                    group_id=group.id,
                    user_id=current_user.id,
                    role="owner",
                    status="active",
                    perm_edit_group=True,
                    perm_manage_members=True,
                    perm_moderate_posts=True,
                    can_create_events=True,
                )
            )
            banner = _save_banner(request.files.get("banner"))
            if banner:
                group.banner_filename = banner
            db.session.commit()
            flash(f'Created "{group.name}".', "success")
            return redirect(url_for("group_view", group_id=group.id))
        return render_template("group_form.html", form={})

    @app.route("/groups/<int:group_id>")
    @login_required
    def group_view(group_id):
        group = _get_group_or_404(group_id)
        me = _membership(group.id, current_user.id, active_only=False)
        if me is not None and me.status == "active":
            return render_template(
                "group_view.html",
                group=group,
                me=me,
                can_settings=_can(me, "edit_group"),
                can_invite=_can(me, "manage_members"),
            )
        # Not (yet) a member: show the join page, but only for groups that
        # advertise themselves or that this user has already requested.
        if not group.advertise_region and me is None:
            abort(404)
        return render_template(
            "group_landing.html", group=group, pending=me is not None, code=None
        )

    @app.route("/groups/invite/<code>")
    @login_required
    def group_invite(code):
        group = Group.query.filter_by(invite_code=code).first()
        if group is None:
            abort(404)
        me = _membership(group.id, current_user.id, active_only=False)
        if me is not None and me.status == "active":
            return redirect(url_for("group_view", group_id=group.id))
        return render_template(
            "group_landing.html", group=group, pending=me is not None, code=code
        )

    @app.route("/groups/<int:group_id>/banner")
    @login_required
    def group_banner(group_id):
        group = _get_group_or_404(group_id)
        if not group.banner_filename:
            abort(404)
        allowed = (
            group.advertise_region
            or _membership(group.id, current_user.id, active_only=False) is not None
            or _code_matches(group, request.args.get("code", ""))
        )
        if not allowed:
            abort(404)
        resp = send_from_directory(
            app.config["UPLOAD_FOLDER"], group.banner_filename, max_age=60 * 60 * 24 * 30
        )
        resp.cache_control.public = False
        resp.cache_control.private = True
        return resp

    @app.route("/groups/<int:group_id>/join", methods=["POST"])
    @login_required
    def group_join(group_id):
        group = _get_group_or_404(group_id)
        if not group.advertise_region and not _code_matches(group, request.form.get("code", "")):
            abort(404)
        existing = _membership(group.id, current_user.id, active_only=False)
        if existing is not None:
            if existing.status == "active":
                flash("You're already in this group.", "error")
            else:
                flash("Your request is already waiting for approval.", "error")
            return redirect(url_for("group_view", group_id=group.id))
        status = "active" if group.join_mode == "open" else "pending"
        db.session.add(
            GroupMember(group_id=group.id, user_id=current_user.id, role="member", status=status)
        )
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()  # double-click / race: membership already exists
            return redirect(url_for("group_view", group_id=group.id))
        if status == "active":
            flash(f'You joined "{group.name}".', "success")
        else:
            flash("Request sent. An admin will review it.", "success")
        return redirect(url_for("group_view", group_id=group.id))

    @app.route("/groups/<int:group_id>/leave", methods=["POST"])
    @login_required
    def group_leave(group_id):
        group = _get_group_or_404(group_id)
        m = _membership(group.id, current_user.id, active_only=False)
        if m is None:
            abort(404)
        if m.role == "owner":
            flash(
                "Owners can't leave their own group. Transfer ownership or delete the group first.",
                "error",
            )
            return redirect(url_for("group_members", group_id=group.id))
        was_pending = m.status == "pending"
        db.session.delete(m)
        db.session.commit()
        flash("Request cancelled." if was_pending else f'You left "{group.name}".', "success")
        return redirect(url_for("groups_home"))

    @app.route("/groups/<int:group_id>/prefs", methods=["POST"])
    @login_required
    def group_prefs(group_id):
        group, me = _require_member(group_id)
        me.share_scores = request.form.get("share_scores") == "on"
        me.email_notify = request.form.get("email_notify") == "on"
        db.session.commit()
        flash("Group preferences saved.", "success")
        return redirect(url_for("group_view", group_id=group.id))

    @app.route("/groups/<int:group_id>/members")
    @login_required
    def group_members(group_id):
        group, me = _require_member(group_id)
        rows = (
            GroupMember.query.filter_by(group_id=group.id)
            .join(User, GroupMember.user_id == User.id)
            .order_by(User.username)
            .all()
        )
        role_order = {"owner": 0, "admin": 1, "member": 2}
        active = sorted(
            (m for m in rows if m.status == "active"),
            key=lambda m: (role_order.get(m.role, 3), m.user.username.lower()),
        )
        can_manage = _can(me, "manage_members")
        pending = [m for m in rows if m.status == "pending"] if can_manage else []
        return render_template(
            "group_members.html",
            group=group,
            me=me,
            members=active,
            pending=pending,
            can_manage=can_manage,
            is_owner=me.role == "owner",
            can_settings=_can(me, "edit_group"),
        )

    @app.route("/groups/<int:group_id>/members/<int:user_id>/<action>", methods=["POST"])
    @login_required
    def group_member_action(group_id, user_id, action):
        group, me = _require_member(group_id)
        target = GroupMember.query.filter_by(group_id=group.id, user_id=user_id).first()
        if target is None:
            abort(404)
        is_owner = me.role == "owner"
        name = target.user.username

        if action in ("approve", "decline"):
            if not _can(me, "manage_members") or target.status != "pending":
                abort(403)
            if action == "approve":
                target.status = "active"
                flash(f"{name} approved.", "success")
            else:
                db.session.delete(target)
                flash(f"Request from {name} declined.", "success")

        elif action == "remove":
            if (
                not _can(me, "manage_members")
                or target.status != "active"
                or target.role == "owner"
                or target.user_id == current_user.id
                or (target.role == "admin" and not is_owner)  # only the owner removes admins
            ):
                abort(403)
            db.session.delete(target)
            flash(f"{name} removed from the group.", "success")

        elif action == "update":  # owner only: admin role, permissions, event access
            if not is_owner or target.status != "active" or target.role == "owner":
                abort(403)
            make_admin = request.form.get("role") == "admin"
            target.role = "admin" if make_admin else "member"
            target.perm_edit_group = make_admin and request.form.get("perm_edit_group") == "on"
            target.perm_manage_members = make_admin and request.form.get("perm_manage_members") == "on"
            target.perm_moderate_posts = make_admin and request.form.get("perm_moderate_posts") == "on"
            target.can_create_events = request.form.get("can_create_events") == "on"
            flash(f"Updated {name}.", "success")

        elif action == "transfer":
            if not is_owner or target.status != "active" or target.role == "owner":
                abort(403)
            target.role = "owner"
            target.perm_edit_group = target.perm_manage_members = target.perm_moderate_posts = True
            target.can_create_events = True
            me.role = "admin"  # previous owner stays on as a fully-permissioned admin
            me.perm_edit_group = me.perm_manage_members = me.perm_moderate_posts = True
            group.owner_id = target.user_id
            flash(f"{name} is now the owner. You're an admin.", "success")

        else:
            abort(404)

        db.session.commit()
        return redirect(url_for("group_members", group_id=group.id))

    @app.route("/groups/<int:group_id>/settings", methods=["GET", "POST"])
    @login_required
    def group_settings(group_id):
        group, me = _require_member(group_id)
        is_owner = me.role == "owner"
        if not _can(me, "edit_group"):
            abort(403)

        if request.method == "POST":
            action = request.form.get("action")

            if action == "details":
                name = request.form.get("name", "").strip()
                if not _valid_group_name(name):
                    flash("Group name must be 3-60 characters.", "error")
                    return redirect(url_for("group_settings", group_id=group.id))
                group.name = name
                group.description = request.form.get("description", "").strip()[:1000] or None
                group.state, group.county, group.advertise_region = _region_from_form()
                new_banner = _save_banner(request.files.get("banner"))
                if new_banner:
                    _delete_photo_file(group.banner_filename)
                    group.banner_filename = new_banner
                elif request.form.get("remove_banner") == "on":
                    _delete_photo_file(group.banner_filename)
                    group.banner_filename = None
                db.session.commit()
                flash("Group details saved.", "success")

            elif action == "joining" and is_owner:
                join_mode = request.form.get("join_mode")
                if join_mode in ("open", "approval"):
                    group.join_mode = join_mode
                policy = request.form.get("event_policy")
                if policy in ("selected", "members"):
                    group.event_policy = policy
                db.session.commit()
                flash("Group rules saved.", "success")

            elif action == "reset_invite" and is_owner:
                group.invite_code = secrets.token_urlsafe(9)
                db.session.commit()
                flash("Invite link reset. The old link no longer works.", "success")

            else:
                abort(403)
            return redirect(url_for("group_settings", group_id=group.id))

        return render_template(
            "group_settings.html", group=group, me=me, is_owner=is_owner, can_settings=True
        )

    @app.route("/groups/<int:group_id>/delete", methods=["POST"])
    @login_required
    def group_delete(group_id):
        group, me = _require_member(group_id)
        if me.role != "owner":
            abort(403)
        if request.form.get("confirm_name", "").strip() != group.name:
            flash("Type the group's exact name to confirm deleting it.", "error")
            return redirect(url_for("group_settings", group_id=group.id))
        name = group.name
        _delete_photo_file(group.banner_filename)
        db.session.delete(group)
        db.session.commit()
        flash(f'Deleted "{name}".', "success")
        return redirect(url_for("groups_home"))

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
        # UDisc's StartDate field looks like "2026-09-25 1630-0400" (date,
        # then time and UTC offset squished together with no separator).
        # We only need the date portion for stats/sorting.
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

        def parse_int(raw_val):
            raw_val = (raw_val or "").strip()
            if raw_val == "":
                return None
            try:
                return int(raw_val)
            except ValueError:
                return None

        # Group CSV rows by round — everyone on the same scorecard shares
        # the same course/layout/start time, so this reunites the group
        # even though the file lists one row per player.
        groups = {}
        order = []
        for row in reader:
            course = (row.get("CourseName") or "").strip()
            layout = (row.get("LayoutName") or "").strip() or None
            played_at = _parse_udisc_date(row.get("StartDate"))
            if not course or played_at is None:
                continue
            key = (course, layout, played_at)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(row)

        added = 0
        skipped_dupe = 0
        skipped_bad = 0

        # Existing rounds for this user, to skip re-importing the same round
        # on a second upload of an overlapping export.
        existing = {
            (r.course_name, r.layout_name, r.played_at, r.total_score)
            for r in Round.query.filter_by(user_id=current_user.id).all()
        }

        for key in order:
            course, layout, played_at = key
            group_rows = groups[key]

            my_row = next(
                (
                    r for r in group_rows
                    if (r.get("PlayerName") or "").strip().lower() == display_name.lower()
                ),
                None,
            )
            if my_row is None:
                continue  # not on this scorecard

            total_score = parse_int(my_row.get("Total"))
            if total_score is None:
                skipped_bad += 1
                continue
            relative_score = parse_int(my_row.get("+/-"))

            dedupe_key = (course, layout, played_at, total_score)
            if dedupe_key in existing:
                skipped_dupe += 1
                continue
            existing.add(dedupe_key)

            holes = []
            for col in hole_columns:
                val = parse_int(my_row.get(col))
                if val is not None:
                    holes.append(val)

            new_round = Round(
                user_id=current_user.id,
                course_name=course,
                layout_name=layout,
                played_at=played_at,
                total_score=total_score,
                relative_score=relative_score,
                hole_scores=json.dumps(holes) if holes else None,
            )
            db.session.add(new_round)
            db.session.flush()  # assign new_round.id for the FK below

            # Capture everyone else on the same scorecard too, so the round
            # view can show the whole group — just as reference data, not
            # tied to any other app account.
            for row in group_rows:
                player = (row.get("PlayerName") or "").strip()
                if not player or player.lower() in (display_name.lower(), "par"):
                    continue
                other_total = parse_int(row.get("Total"))
                if other_total is None:
                    continue
                db.session.add(
                    RoundPlayerScore(
                        round_id=new_round.id,
                        player_name=player,
                        total_score=other_total,
                        relative_score=parse_int(row.get("+/-")),
                    )
                )

            added += 1

        return added, skipped_dupe, skipped_bad

    def _compute_round_stats(user_rounds):
        if not user_rounds:
            return {
                "avg_by_course": [],
                "leaderboard": [],
                "total_rounds": 0,
                "best_relative": None,
            }

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

        # Leaderboard: courses ranked by average relative score (+/-), best
        # (most under par) first. Only courses with at least one round that
        # has a relative score are eligible.
        leaderboard_rows = []
        for course, rs in by_course.items():
            rel_scores = [x.relative_score for x in rs if x.relative_score is not None]
            if not rel_scores:
                continue
            leaderboard_rows.append(
                {
                    "course_name": course,
                    "round_count": len(rel_scores),
                    "avg_relative": round(sum(rel_scores) / len(rel_scores), 1),
                }
            )
        leaderboard_rows.sort(key=lambda x: x["avg_relative"])
        for i, row in enumerate(leaderboard_rows, start=1):
            row["rank"] = i

        relative_scores = [r.relative_score for r in user_rounds if r.relative_score is not None]
        best_relative = min(relative_scores) if relative_scores else None

        return {
            "avg_by_course": avg_by_course,
            "leaderboard": leaderboard_rows,
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

    def _handle_avatar_upload(user):
        """Resize an uploaded profile picture to a small square JPEG.
        Re-encoding through Pillow also strips EXIF metadata (phone photos
        often embed GPS coordinates), which matters since avatars are
        visible to other users."""
        file = request.files.get("profile_photo")
        if not file or not file.filename:
            return
        if not _allowed_photo(file.filename):
            flash("Profile picture not saved: use a JPG, PNG, or WEBP image.", "error")
            return
        try:
            img = Image.open(file.stream)
            img = ImageOps.exif_transpose(img)  # respect phone rotation
            img = ImageOps.fit(img.convert("RGB"), (AVATAR_SIZE, AVATAR_SIZE))
        except Exception:
            flash("Profile picture not saved: that file couldn't be read as an image.", "error")
            return
        new_filename = f"avatar_{uuid.uuid4().hex}.jpg"
        img.save(
            os.path.join(app.config["UPLOAD_FOLDER"], new_filename),
            "JPEG",
            quality=88,
        )
        _delete_photo_file(user.profile_photo)
        user.profile_photo = new_filename

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
