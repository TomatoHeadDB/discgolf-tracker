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

    discs = db.relationship(
        "Disc", backref="owner", lazy=True, cascade="all, delete-orphan"
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
