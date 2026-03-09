from datetime import datetime
import os

from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///voting.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)


class Election(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Candidate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    election_id = db.Column(db.Integer, db.ForeignKey("election.id"), nullable=False)


class Vote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    election_id = db.Column(db.Integer, db.ForeignKey("election.id"), nullable=False)
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidate.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("user_id", "election_id", name="uq_user_election_vote"),)


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return User.query.get(user_id)


def login_required():
    if not current_user():
        flash("Please log in first.", "warning")
        return False
    return True


@app.context_processor
def inject_user_context():
    return {"user": current_user()}


def admin_required():
    user = current_user()
    if not user or not user.is_admin:
        flash("Admin access required.", "danger")
        return False
    return True


@app.route("/")
def index():
    elections = Election.query.order_by(Election.created_at.desc()).all()
    user = current_user()
    voted_election_ids = set()
    if user:
        voted_election_ids = {v.election_id for v in Vote.query.filter_by(user_id=user.id).all()}
    return render_template("index.html", elections=elections, user=user, voted_election_ids=voted_election_ids)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("All fields are required.", "danger")
            return redirect(url_for("register"))

        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "warning")
            return redirect(url_for("register"))

        user = User(name=name, email=email, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid credentials.", "danger")
            return redirect(url_for("login"))

        session["user_id"] = user.id
        flash("Logged in successfully.", "success")
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("index"))


@app.route("/admin/create_election", methods=["GET", "POST"])
def create_election():
    if not admin_required():
        return redirect(url_for("index"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        candidates_raw = request.form.get("candidates", "")

        candidates = [c.strip() for c in candidates_raw.splitlines() if c.strip()]

        if not title or not description or len(candidates) < 2:
            flash("Provide title, description, and at least 2 candidates.", "danger")
            return redirect(url_for("create_election"))

        election = Election(title=title, description=description)
        db.session.add(election)
        db.session.flush()

        for candidate_name in candidates:
            db.session.add(Candidate(name=candidate_name, election_id=election.id))

        db.session.commit()
        flash("Election created.", "success")
        return redirect(url_for("index"))

    return render_template("create_election.html")


@app.route("/election/<int:election_id>", methods=["GET", "POST"])
def election_detail(election_id: int):
    if not login_required():
        return redirect(url_for("login"))

    election = Election.query.get_or_404(election_id)
    candidates = Candidate.query.filter_by(election_id=election_id).all()
    user = current_user()

    existing_vote = Vote.query.filter_by(user_id=user.id, election_id=election_id).first()

    if request.method == "POST":
        if existing_vote:
            flash("You have already voted in this election.", "warning")
            return redirect(url_for("election_detail", election_id=election_id))

        candidate_id = request.form.get("candidate_id", type=int)
        candidate = Candidate.query.filter_by(id=candidate_id, election_id=election_id).first()

        if not candidate:
            flash("Invalid candidate selection.", "danger")
            return redirect(url_for("election_detail", election_id=election_id))

        vote = Vote(user_id=user.id, election_id=election_id, candidate_id=candidate.id)
        db.session.add(vote)
        db.session.commit()
        flash("Vote submitted successfully.", "success")
        return redirect(url_for("results", election_id=election_id))

    return render_template("election_detail.html", election=election, candidates=candidates, existing_vote=existing_vote)


@app.route("/results/<int:election_id>")
def results(election_id: int):
    election = Election.query.get_or_404(election_id)
    candidates = Candidate.query.filter_by(election_id=election_id).all()

    results_data = []
    total_votes = Vote.query.filter_by(election_id=election_id).count()
    for candidate in candidates:
        count = Vote.query.filter_by(election_id=election_id, candidate_id=candidate.id).count()
        percentage = (count / total_votes * 100) if total_votes else 0
        results_data.append({"name": candidate.name, "count": count, "percentage": percentage})

    results_data.sort(key=lambda item: item["count"], reverse=True)
    return render_template("results.html", election=election, results_data=results_data, total_votes=total_votes)


def bootstrap_admin():
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
    existing = User.query.filter_by(email=admin_email).first()
    if not existing:
        admin = User(
            name="Admin",
            email=admin_email,
            password_hash=generate_password_hash(admin_password),
            is_admin=True,
        )
        db.session.add(admin)
        db.session.commit()


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        bootstrap_admin()
    app.run(host="0.0.0.0", port=5000, debug=True)
