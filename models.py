import json
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    # Player name as it appears on UDisc scorecards, used to filter which
    # rows belong to this user when importing a scorecard CSV that may
    # include other players too.
    udisc_display_name = db.Column(db.String(120), nullable=True)
    bag_public = db.Column(db.Boolean, default=False, nullable=False)

    discs = db.relationship(
        "Disc", backref="owner", lazy=True, cascade="all, delete-orphan"
    )
    rounds = db.relationship(
        "Round", backref="owner", lazy=True, cascade="all, delete-orphan"
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Disc(db.Model):
    __tablename__ = "discs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    name = db.Column(db.String(120), nullable=False)
    manufacturer = db.Column(db.String(120), nullable=True)
    category = db.Column(db.String(30), nullable=False, default="Midrange")
    # PDGA-style flight numbers
    speed = db.Column(db.Float, nullable=False, default=5.0)
    glide = db.Column(db.Float, nullable=False, default=4.0)
    turn = db.Column(db.Float, nullable=False, default=0.0)
    fade = db.Column(db.Float, nullable=False, default=2.0)

    plastic = db.Column(db.String(80), nullable=True)
    color = db.Column(db.String(20), nullable=False, default="#3b82f6")
    weight_grams = db.Column(db.Integer, nullable=True)
    condition = db.Column(db.String(30), nullable=True)
    in_bag = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text, nullable=True)
    photo_filename = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    throws = db.relationship(
        "ThrowMeasurement", backref="disc", lazy=True, cascade="all, delete-orphan"
    )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "manufacturer": self.manufacturer,
            "category": self.category,
            "speed": self.speed,
            "glide": self.glide,
            "turn": self.turn,
            "fade": self.fade,
            "plastic": self.plastic,
            "color": self.color,
            "weight_grams": self.weight_grams,
            "condition": self.condition,
            "in_bag": self.in_bag,
            "notes": self.notes,
        }


class Round(db.Model):
    __tablename__ = "rounds"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    course_name = db.Column(db.String(200), nullable=False)
    layout_name = db.Column(db.String(120), nullable=True)
    played_at = db.Column(db.Date, nullable=False, index=True)
    total_score = db.Column(db.Integer, nullable=False)
    relative_score = db.Column(db.Integer, nullable=True)  # e.g. -3, +2
    hole_scores = db.Column(db.Text, nullable=True)  # JSON-encoded list of ints

    imported_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    player_scores = db.relationship(
        "RoundPlayerScore", backref="round", lazy=True, cascade="all, delete-orphan"
    )

    def to_dict(self):
        return {
            "id": self.id,
            "course_name": self.course_name,
            "layout_name": self.layout_name,
            "played_at": self.played_at.isoformat() if self.played_at else None,
            "total_score": self.total_score,
            "relative_score": self.relative_score,
            "hole_scores": json.loads(self.hole_scores) if self.hole_scores else [],
        }


class RoundPlayerScore(db.Model):
    """A companion score for someone else on the same scorecard as the
    round owner. Not a full app user — just a name + score captured from
    the CSV so the round view can show the whole group, not just you."""

    __tablename__ = "round_player_scores"

    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey("rounds.id"), nullable=False, index=True)
    player_name = db.Column(db.String(120), nullable=False)
    total_score = db.Column(db.Integer, nullable=False)
    relative_score = db.Column(db.Integer, nullable=True)


class ThrowMeasurement(db.Model):
    """A single GPS-measured throw distance for a specific disc, used to
    calibrate that disc's flight preview to how far the player actually
    throws it."""

    __tablename__ = "throw_measurements"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    disc_id = db.Column(db.Integer, db.ForeignKey("discs.id"), nullable=False, index=True)
    distance_feet = db.Column(db.Float, nullable=False)
    recorded_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Connection(db.Model):
    """A social connection between two users. 'pending' until the
    recipient accepts, then 'accepted' — accepted connections show up in
    each other's combined course leaderboards and can see each other's
    public bag."""

    __tablename__ = "connections"

    id = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending | accepted
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    requester = db.relationship("User", foreign_keys=[requester_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])
