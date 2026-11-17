from flask import (
    Flask, render_template, request, redirect,
    flash, session, url_for
)

from database import db, User, MealSlot, Booking, Notification

from werkzeug.security import generate_password_hash, check_password_hash

from datetime import datetime, timedelta

import qrcode
import os
import time


app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///queueless.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.secret_key = os.environ.get("SECRET_KEY", "queueless-demo-secret")

db.init_app(app)


# ---------------- DATABASE ----------------

with app.app_context():
    os.makedirs("static/qr_codes", exist_ok=True)
    db.create_all()


# ---------------- HELPER FUNCTIONS ----------------

def login_required():
    return "user_id" in session


def role_required(*roles):
    return login_required() and session.get("role") in roles


def notify_user(user_id, message):
    notification = Notification(
        user_id=user_id,
        message=message
    )
    db.session.add(notification)


def check_no_shows():
    """Mark bookings as No-show after the 10-minute grace period."""

    now = datetime.now()

    bookings = Booking.query.filter_by(status="Booked").all()

    for booking in bookings:
        try:
            slot_datetime = datetime.strptime(
                booking.slot.date + " " + booking.slot.start_time,
                "%Y-%m-%d %H:%M"
            )

            deadline = slot_datetime + timedelta(minutes=10)

            if now > deadline:
                booking.status = "No-show"

                notify_user(
                    booking.user_id,
                    f"Your {booking.slot.meal_type} reservation was marked as No-show."
                )

        except (ValueError, AttributeError):
            continue

    db.session.commit()


# Run the no-show check periodically when requests reach the application.
_last_no_show_check = 0


@app.before_request
def automatic_no_show_check():
    global _last_no_show_check

    if time.time() - _last_no_show_check >= 60:
        with app.app_context():
            check_no_shows()

        _last_no_show_check = time.time()


# ---------------- HOME ----------------

@app.route("/")
def home():
    return render_template("login.html")


# ---------------- REGISTER ----------------

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "student").lower()

        if role not in ["student", "teacher"]:
            flash("Invalid registration role.")
            return redirect(url_for("register"))

        if not name or not email or not password:
            flash("Please fill all fields.")
            return redirect(url_for("register"))

        existing_user = User.query.filter_by(email=email).first()

        if existing_user:
            flash("Email already registered.")
            return redirect(url_for("register"))

        user = User(
            name=name,
            email=email,
            password=generate_password_hash(password),
            role=role
        )

        db.session.add(user)
        db.session.commit()

        flash("Registration successful. Please login.")
        return redirect(url_for("login"))

    return render_template("register.html")


# ---------------- LOGIN ----------------

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()

        if user and check_password_hash(user.password, password):

            session.clear()
            session["user_id"] = user.id
            session["name"] = user.name
            session["role"] = user.role

            flash("Login successful.")

            if user.role == "admin":
                return redirect(url_for("admin_dashboard"))

            if user.role == "teacher":
                return redirect(url_for("staff_dashboard"))

            return redirect(url_for("dashboard"))

        flash("Invalid email or password.")

    return render_template("login.html")


# ---------------- LOGOUT ----------------

@app.route("/logout")
def logout():

    session.clear()

    flash("Logged out successfully.")

    return redirect(url_for("login"))


# ---------------- STUDENT DASHBOARD ----------------

@app.route("/dashboard")
def dashboard():

    if not role_required("student"):
        return redirect(url_for("login"))

    return render_template(
        "dashboard.html",
        name=session["name"]
    )


# ---------------- BOOK MEAL ----------------

@app.route("/book", methods=["GET", "POST"])
def book():

    if not role_required("student"):
        return redirect(url_for("login"))

    if request.method == "POST":

        meal_type = request.form.get("meal_type")
        date = request.form.get("date")

        if meal_type not in ["Breakfast", "Lunch", "Dinner"]:
            flash("Invalid meal type.")
            return redirect(url_for("book"))

        try:
            selected_date = datetime.strptime(date, "%Y-%m-%d").date()

            if selected_date < datetime.today().date():
                flash("Please select a future date.")
                return redirect(url_for("book"))

        except (ValueError, TypeError):
            flash("Please select a valid date.")
            return redirect(url_for("book"))

        # Find the meal slot
        slot = MealSlot.query.filter_by(
            meal_type=meal_type,
            date=date
        ).first()

        # Create a default slot if it does not exist
        if not slot:

            times = {
                "Breakfast": "08:00",
                "Lunch": "12:00",
                "Dinner": "19:00"
            }

            slot = MealSlot(
                meal_type=meal_type,
                date=date,
                start_time=times[meal_type],
                capacity=50
            )

            db.session.add(slot)
            db.session.commit()

        # Check existing active bookings
        active_bookings = Booking.query.filter(
            Booking.slot_id == slot.id,
            Booking.status.in_(["Booked", "Checked-in"])
        ).count()

        if active_bookings >= slot.capacity:
            flash("Sorry! This meal slot is full.")
            return redirect(url_for("book"))

        # Prevent duplicate booking
        existing = Booking.query.filter_by(
            user_id=session["user_id"],
            slot_id=slot.id,
            status="Booked"
        ).first()

        if existing:
            flash("You already have a reservation for this slot.")
            return redirect(url_for("my_bookings"))

        booking = Booking(
            user_id=session["user_id"],
            slot_id=slot.id,
            status="Booked",
            booking_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

        db.session.add(booking)
        db.session.commit()

        # Generate QR code
        qr_data = url_for(
            "verify_booking",
            booking_id=booking.id,
            _external=True
        )

        qr = qrcode.make(qr_data)

        qr_path = f"static/qr_codes/booking_{booking.id}.png"

        qr.save(qr_path)

        notify_user(
            session["user_id"],
            f"Your {meal_type} reservation for {date} is confirmed."
        )

        db.session.commit()

        flash("Meal booked successfully!")

        return redirect(url_for("my_bookings"))

    return render_template("booking.html")


# ---------------- MY BOOKINGS ----------------

@app.route("/my-bookings")
def my_bookings():

    if not role_required("student"):
        return redirect(url_for("login"))

    bookings = Booking.query.filter_by(
        user_id=session["user_id"]
    ).order_by(Booking.id.desc()).all()

    return render_template(
        "my_bookings.html",
        bookings=bookings
    )


# ---------------- CANCEL BOOKING ----------------

@app.route("/cancel/<int:booking_id>", methods=["POST"])
def cancel_booking(booking_id):

    if not role_required("student"):
        return redirect(url_for("login"))

    booking = Booking.query.filter_by(
        id=booking_id,
        user_id=session["user_id"]
    ).first()

    if not booking:
        flash("Booking not found.")
        return redirect(url_for("my_bookings"))

    if booking.status != "Booked":
        flash("This booking cannot be cancelled.")
        return redirect(url_for("my_bookings"))

    booking.status = "Cancelled"

    notify_user(
        session["user_id"],
        f"Your {booking.slot.meal_type} reservation was cancelled."
    )

    db.session.commit()

    flash("Booking cancelled. The slot is available again.")

    return redirect(url_for("my_bookings"))


# ---------------- QR VERIFICATION ----------------

@app.route("/verify/<int:booking_id>")
def verify_booking(booking_id):

    if not role_required("teacher", "admin"):
        flash("Staff login required.")
        return redirect(url_for("login"))

    booking = db.session.get(Booking, booking_id)

    return render_template(
        "verify.html",
        booking=booking
    )


# ---------------- CHECK-IN ----------------

@app.route("/check-in/<int:booking_id>", methods=["POST"])
def check_in(booking_id):

    if not role_required("teacher", "admin"):
        return redirect(url_for("login"))

    booking = db.session.get(Booking, booking_id)

    if not booking:
        flash("Invalid reservation.")
        return redirect(url_for("staff_dashboard"))

    if booking.status != "Booked":
        flash("This reservation is already used or no longer valid.")
        return redirect(url_for("staff_dashboard"))

    booking.status = "Checked-in"

    notify_user(
        booking.user_id,
        "Your meal reservation has been successfully verified."
    )

    db.session.commit()

    flash("Reservation verified. Student checked in.")

    return redirect(url_for("staff_dashboard"))


# ---------------- STAFF DASHBOARD ----------------

@app.route("/staff")
def staff_dashboard():

    if not role_required("teacher", "admin"):
        return redirect(url_for("login"))

    bookings = Booking.query.order_by(Booking.id.desc()).all()

    return render_template(
        "staff.html",
        bookings=bookings
    )


# ---------------- NOTIFICATIONS ----------------

@app.route("/notifications")
def notifications():

    if not login_required():
        return redirect(url_for("login"))

    notifications = Notification.query.filter_by(
        user_id=session["user_id"]
    ).order_by(Notification.created_at.desc()).all()

    for notification in notifications:
        notification.is_read = True

    db.session.commit()

    return render_template(
        "notifications.html",
        notifications=notifications
    )


# ---------------- ADMIN DASHBOARD ----------------

@app.route("/admin")
def admin_dashboard():

    if not role_required("admin"):
        return redirect(url_for("login"))

    total_users = User.query.count()
    total_bookings = Booking.query.count()

    active_bookings = Booking.query.filter(
        Booking.status.in_(["Booked", "Checked-in"])
    ).count()

    checked_in = Booking.query.filter_by(
        status="Checked-in"
    ).count()

    no_shows = Booking.query.filter_by(
        status="No-show"
    ).count()

    return render_template(
        "admin.html",
        total_users=total_users,
        total_bookings=total_bookings,
        active_bookings=active_bookings,
        checked_in=checked_in,
        no_shows=no_shows
    )


# ---------------- ADMIN CREATE MEAL SLOT ----------------

@app.route("/admin/create-slot", methods=["GET", "POST"])
def create_slot():

    if not role_required("admin"):
        return redirect(url_for("login"))

    if request.method == "POST":

        meal_type = request.form.get("meal_type")
        date = request.form.get("date")
        start_time = request.form.get("start_time")
        capacity = request.form.get("capacity")

        try:
            capacity = int(capacity)

            if capacity < 1:
                raise ValueError

            datetime.strptime(date, "%Y-%m-%d")
            datetime.strptime(start_time, "%H:%M")

        except (ValueError, TypeError):
            flash("Please enter valid slot details.")
            return redirect(url_for("create_slot"))

        if meal_type not in ["Breakfast", "Lunch", "Dinner"]:
            flash("Invalid meal type.")
            return redirect(url_for("create_slot"))

        existing = MealSlot.query.filter_by(
            meal_type=meal_type,
            date=date
        ).first()

        if existing:
            flash("This meal slot already exists.")
            return redirect(url_for("create_slot"))

        slot = MealSlot(
            meal_type=meal_type,
            date=date,
            start_time=start_time,
            capacity=capacity
        )

        db.session.add(slot)
        db.session.commit()

        flash("Meal slot created successfully.")

        return redirect(url_for("admin_dashboard"))

    return render_template("create_slot.html")


# ---------------- ADMIN REPORT ----------------

@app.route("/admin/report")
def admin_report():

    if not role_required("admin"):
        return redirect(url_for("login"))

    bookings = Booking.query.order_by(Booking.id.desc()).all()

    return render_template(
        "report.html",
        bookings=bookings
    )


# ---------------- ADMIN USER CREATION ----------------

@app.route("/admin/create-user", methods=["GET", "POST"])
def create_admin_user():

    if not role_required("admin"):
        return redirect(url_for("login"))

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role")

        if role not in ["student", "teacher", "admin"]:
            flash("Invalid role.")
            return redirect(url_for("create_admin_user"))

        if not name or not email or not password:
            flash("Please fill all fields.")
            return redirect(url_for("create_admin_user"))

        if User.query.filter_by(email=email).first():
            flash("Email already exists.")
            return redirect(url_for("create_admin_user"))

        user = User(
            name=name,
            email=email,
            password=generate_password_hash(password),
            role=role
        )

        db.session.add(user)
        db.session.commit()

        flash("User created successfully.")

        return redirect(url_for("admin_dashboard"))

    return render_template("create_user.html")


# ---------------- RUN APPLICATION ----------------

if __name__ == "__main__":
    app.run(debug=True)