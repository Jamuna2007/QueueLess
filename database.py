from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)


class MealSlot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    meal_type = db.Column(db.String(20), nullable=False)
    date = db.Column(db.String(20), nullable=False)
    start_time = db.Column(db.String(20), nullable=False)
    capacity = db.Column(db.Integer, nullable=False)


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    slot_id = db.Column(db.Integer, db.ForeignKey("meal_slot.id"))
    status = db.Column(db.String(20), default="Booked")
    booking_time = db.Column(db.String(30))

    user = db.relationship("User")
    slot = db.relationship("MealSlot")


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    message = db.Column(db.String(250), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)

    user = db.relationship("User")