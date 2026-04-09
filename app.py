import os
import json
import secrets
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from functools import wraps
from statistics import mean

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import and_, func, or_
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'voting.db')}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024


db = SQLAlchemy(app)


# -----------------------------------------------------------------------------
# Database models
# -----------------------------------------------------------------------------


class TimeStampedModel:
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class User(TimeStampedModel, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(180), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    bio = db.Column(db.Text, default="", nullable=False)
    timezone = db.Column(db.String(50), default="UTC", nullable=False)
    avatar_seed = db.Column(db.String(64), default=lambda: secrets.token_hex(8), nullable=False)
    last_login_at = db.Column(db.DateTime(timezone=True), nullable=True)


class Election(TimeStampedModel, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(240), unique=True, nullable=False, index=True)
    title = db.Column(db.String(240), nullable=False)
    description = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(80), nullable=False, default="general")
    visibility = db.Column(db.String(20), nullable=False, default="public")
    status = db.Column(db.String(20), nullable=False, default="draft")
    start_at = db.Column(db.DateTime(timezone=True), nullable=False)
    end_at = db.Column(db.DateTime(timezone=True), nullable=False)
    allow_abstain = db.Column(db.Boolean, default=False, nullable=False)
    allow_live_results = db.Column(db.Boolean, default=True, nullable=False)
    max_choices = db.Column(db.Integer, default=1, nullable=False)
    banner = db.Column(db.String(255), default="aurora", nullable=False)
    rules = db.Column(db.Text, default="", nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Candidate(TimeStampedModel, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    election_id = db.Column(db.Integer, db.ForeignKey("election.id"), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    slogan = db.Column(db.String(255), default="", nullable=False)
    manifesto = db.Column(db.Text, default="", nullable=False)
    experience_years = db.Column(db.Integer, default=0, nullable=False)
    icon = db.Column(db.String(80), default="spark", nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)


class ElectionVoter(TimeStampedModel, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    election_id = db.Column(db.Integer, db.ForeignKey("election.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    invitation_status = db.Column(db.String(20), default="approved", nullable=False)

    __table_args__ = (db.UniqueConstraint("election_id", "user_id", name="uq_election_voter"),)


class Vote(TimeStampedModel, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    election_id = db.Column(db.Integer, db.ForeignKey("election.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidate.id"), nullable=True, index=True)
    rank = db.Column(db.Integer, default=1, nullable=False)
    vote_token = db.Column(db.String(64), nullable=False, unique=True)
    client_fingerprint = db.Column(db.String(255), default="", nullable=False)

    __table_args__ = (
        db.UniqueConstraint("election_id", "user_id", "rank", name="uq_vote_rank_per_user"),
    )


class AuditLog(TimeStampedModel, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)
    action = db.Column(db.String(120), nullable=False)
    target_type = db.Column(db.String(60), nullable=False)
    target_id = db.Column(db.Integer, nullable=True)
    metadata_json = db.Column(db.Text, default="{}", nullable=False)
    ip_address = db.Column(db.String(80), default="", nullable=False)


class Announcement(TimeStampedModel, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    is_published = db.Column(db.Boolean, default=True, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


# -----------------------------------------------------------------------------
# Utility helpers
# -----------------------------------------------------------------------------


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime | None) -> str | None:
    if not dt:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if len(value) == 16:
            dt = datetime.strptime(value, "%Y-%m-%dT%H:%M")
            return dt.replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def slugify(title: str) -> str:
    raw = "".join(ch.lower() if ch.isalnum() else "-" for ch in title)
    raw = "-".join(part for part in raw.split("-") if part)
    if not raw:
        raw = "election"
    base = raw[:80]
    probe = base
    counter = 2
    while Election.query.filter_by(slug=probe).first():
        probe = f"{base}-{counter}"
        counter += 1
    return probe


def election_state(election: Election) -> str:
    now = utcnow()
    if election.status == "archived":
        return "archived"
    if now < election.start_at:
        return "upcoming"
    if election.start_at <= now <= election.end_at:
        return "live"
    return "ended"


def election_badge(state: str) -> str:
    return {
        "draft": "badge-gray",
        "upcoming": "badge-blue",
        "live": "badge-green",
        "ended": "badge-purple",
        "archived": "badge-dark",
    }.get(state, "badge-gray")


def get_client_fingerprint() -> str:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    ua = request.headers.get("User-Agent", "")
    lang = request.headers.get("Accept-Language", "")
    return f"{ip}::{ua[:100]}::{lang[:40]}"


def log_action(action: str, target_type: str, target_id: int | None = None, metadata: dict | None = None) -> None:
    actor_id = session.get("user_id")
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "") if request else ""
    entry = AuditLog(
        actor_user_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
        ip_address=ip,
    )
    db.session.add(entry)


def parse_json_field(value: str) -> dict:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
        if isinstance(decoded, dict):
            return decoded
        return {"value": decoded}
    except json.JSONDecodeError:
        return {"raw": value}


def require_login(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        return func(*args, **kwargs)

    return wrapper


def require_admin(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or not user.is_admin:
            flash("Admin access is required for that page.", "danger")
            return redirect(url_for("index"))
        return func(*args, **kwargs)

    return wrapper


def current_user() -> User | None:
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)


def is_eligible_for_election(user: User, election: Election) -> bool:
    if election.visibility == "public":
        return True
    allowed = ElectionVoter.query.filter_by(election_id=election.id, user_id=user.id).first()
    return bool(allowed)


def summarize_recent_activity(limit: int = 12) -> list[dict]:
    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(limit).all()
    mapped = []
    for log in logs:
        actor = User.query.get(log.actor_user_id) if log.actor_user_id else None
        mapped.append(
            {
                "id": log.id,
                "action": log.action,
                "target_type": log.target_type,
                "target_id": log.target_id,
                "actor": actor.name if actor else "System",
                "time": log.created_at,
                "metadata": parse_json_field(log.metadata_json),
            }
        )
    return mapped


def calculate_turnout(election_id: int) -> dict:
    election = Election.query.get_or_404(election_id)
    total_cast = Vote.query.filter_by(election_id=election.id, rank=1).count()

    if election.visibility == "public":
        eligible = User.query.count()
    else:
        eligible = ElectionVoter.query.filter_by(election_id=election.id).count()

    turnout = (total_cast / eligible * 100) if eligible else 0.0
    return {
        "eligible": eligible,
        "votes": total_cast,
        "turnout": round(turnout, 2),
    }


def get_election_candidates(election_id: int) -> list[Candidate]:
    return Candidate.query.filter_by(election_id=election_id).order_by(Candidate.sort_order.asc(), Candidate.id.asc()).all()


def get_voted_candidate_ids(user_id: int, election_id: int) -> list[int]:
    votes = Vote.query.filter_by(user_id=user_id, election_id=election_id).all()
    return [v.candidate_id for v in votes if v.candidate_id]


def compute_results(election_id: int) -> dict:
    candidates = get_election_candidates(election_id)
    votes = Vote.query.filter_by(election_id=election_id, rank=1).all()
    total_votes = len(votes)
    counter = Counter(v.candidate_id for v in votes if v.candidate_id)

    rows = []
    for c in candidates:
        count = counter.get(c.id, 0)
        percentage = (count / total_votes * 100) if total_votes else 0
        rows.append(
            {
                "candidate_id": c.id,
                "name": c.name,
                "slogan": c.slogan,
                "count": count,
                "percentage": round(percentage, 2),
            }
        )

    rows.sort(key=lambda x: x["count"], reverse=True)
    winner = rows[0] if rows else None

    return {
        "rows": rows,
        "total_votes": total_votes,
        "winner": winner,
    }


def parse_candidates_from_form(raw: str) -> list[dict]:
    """
    Input format (one candidate per line):
    Name | Slogan | Experience Years | Manifesto
    """
    parsed = []
    for idx, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        name = parts[0] if len(parts) > 0 else ""
        slogan = parts[1] if len(parts) > 1 else ""
        exp_raw = parts[2] if len(parts) > 2 else "0"
        manifesto = parts[3] if len(parts) > 3 else ""
        try:
            experience = max(0, int(exp_raw or "0"))
        except ValueError:
            experience = 0
        parsed.append(
            {
                "name": name[:150],
                "slogan": slogan[:255],
                "experience_years": experience,
                "manifesto": manifesto[:3000],
                "sort_order": idx,
            }
        )
    return parsed


def election_filters(query, status: str, q: str, category: str):
    now = utcnow()
    if status == "live":
        query = query.filter(and_(Election.start_at <= now, Election.end_at >= now, Election.status != "archived"))
    elif status == "upcoming":
        query = query.filter(and_(Election.start_at > now, Election.status != "archived"))
    elif status == "ended":
        query = query.filter(and_(Election.end_at < now, Election.status != "archived"))
    elif status == "archived":
        query = query.filter(Election.status == "archived")

    if category and category != "all":
        query = query.filter(Election.category == category)

    if q:
        q_like = f"%{q.strip()}%"
        query = query.filter(or_(Election.title.ilike(q_like), Election.description.ilike(q_like)))

    return query


def notify_flash_for_state(state: str) -> None:
    if state == "upcoming":
        flash("This election has not opened yet. You can preview details and candidates.", "info")
    elif state == "ended":
        flash("This election has ended. Results are available below.", "info")
    elif state == "archived":
        flash("This election is archived and read-only.", "secondary")


# -----------------------------------------------------------------------------
# Context injection
# -----------------------------------------------------------------------------


@app.context_processor
def inject_globals():
    user = current_user()
    active_elections = Election.query.count()
    users_count = User.query.count()
    votes_count = Vote.query.filter_by(rank=1).count()
    announcements = Announcement.query.filter_by(is_published=True).order_by(Announcement.created_at.desc()).limit(5).all()

    return {
        "user": user,
        "platform_stats": {
            "elections": active_elections,
            "users": users_count,
            "votes": votes_count,
        },
        "latest_announcements": announcements,
        "election_state": election_state,
        "election_badge": election_badge,
        "utcnow": utcnow,
        "to_iso": to_iso,
    }


# -----------------------------------------------------------------------------
# Authentication
# -----------------------------------------------------------------------------


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if len(name) < 2:
            flash("Name must be at least 2 characters.", "danger")
            return redirect(url_for("register"))
        if "@" not in email or len(email) > 180:
            flash("Please provide a valid email address.", "danger")
            return redirect(url_for("register"))
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "danger")
            return redirect(url_for("register"))
        if password != confirm:
            flash("Password confirmation does not match.", "danger")
            return redirect(url_for("register"))
        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "warning")
            return redirect(url_for("register"))

        new_user = User(name=name, email=email, password_hash=generate_password_hash(password))
        db.session.add(new_user)
        log_action("register", "user", metadata={"email": email})
        db.session.commit()

        flash("Your account was created successfully. You can now log in.", "success")
        return redirect(url_for("login"))

    return render_template("auth/register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid email or password.", "danger")
            return redirect(url_for("login"))

        session.clear()
        session["user_id"] = user.id
        session["csrf_token"] = secrets.token_hex(16)
        user.last_login_at = utcnow()
        log_action("login", "user", user.id)
        db.session.commit()

        next_url = request.args.get("next")
        flash(f"Welcome back, {user.name}!", "success")
        return redirect(next_url or url_for("dashboard"))

    return render_template("auth/login.html")


@app.route("/logout")
def logout():
    uid = session.get("user_id")
    session.clear()
    if uid:
        log_action("logout", "user", uid)
        db.session.commit()
    flash("You are now signed out.", "info")
    return redirect(url_for("index"))


@app.route("/profile", methods=["GET", "POST"])
@require_login
def profile():
    user = current_user()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        bio = request.form.get("bio", "").strip()
        timezone_value = request.form.get("timezone", "UTC").strip() or "UTC"

        if len(name) < 2:
            flash("Display name is too short.", "danger")
            return redirect(url_for("profile"))

        user.name = name
        user.bio = bio[:2000]
        user.timezone = timezone_value[:50]
        user.avatar_seed = request.form.get("avatar_seed", user.avatar_seed)[:64]
        log_action("update_profile", "user", user.id)
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))

    my_votes = (
        db.session.query(Election.title, Vote.created_at)
        .join(Vote, Vote.election_id == Election.id)
        .filter(Vote.user_id == user.id, Vote.rank == 1)
        .order_by(Vote.created_at.desc())
        .limit(10)
        .all()
    )
    return render_template("profile.html", my_votes=my_votes)


# -----------------------------------------------------------------------------
# Public pages
# -----------------------------------------------------------------------------


@app.route("/")
def index():
    featured = Election.query.order_by(Election.created_at.desc()).limit(6).all()
    leaderboard = (
        db.session.query(User.name, func.count(Vote.id).label("vote_count"))
        .join(Vote, Vote.user_id == User.id)
        .filter(Vote.rank == 1)
        .group_by(User.id)
        .order_by(func.count(Vote.id).desc())
        .limit(5)
        .all()
    )

    insights = {
        "live": sum(1 for e in featured if election_state(e) == "live"),
        "upcoming": sum(1 for e in featured if election_state(e) == "upcoming"),
        "ended": sum(1 for e in featured if election_state(e) == "ended"),
    }

    return render_template("index.html", featured=featured, leaderboard=leaderboard, insights=insights)


@app.route("/dashboard")
@require_login
def dashboard():
    user = current_user()
    status = request.args.get("status", "all")
    q = request.args.get("q", "")
    category = request.args.get("category", "all")

    query = Election.query.order_by(Election.start_at.desc())
    query = election_filters(query, status, q, category)

    elections = query.limit(120).all()
    my_votes = {v.election_id: v for v in Vote.query.filter_by(user_id=user.id, rank=1).all()}

    stats = {
        "total": len(elections),
        "voted": sum(1 for e in elections if e.id in my_votes),
        "live": sum(1 for e in elections if election_state(e) == "live"),
        "upcoming": sum(1 for e in elections if election_state(e) == "upcoming"),
    }

    categories = [
        "general",
        "technology",
        "sports",
        "education",
        "governance",
        "culture",
        "community",
        "product",
    ]

    return render_template(
        "dashboard.html",
        elections=elections,
        my_votes=my_votes,
        stats=stats,
        status=status,
        q=q,
        category=category,
        categories=categories,
    )


@app.route("/elections/<slug>", methods=["GET", "POST"])
@require_login
def election_detail(slug: str):
    user = current_user()
    election = Election.query.filter_by(slug=slug).first_or_404()

    if not is_eligible_for_election(user, election):
        flash("You are not eligible for this private election.", "danger")
        return redirect(url_for("dashboard"))

    state = election_state(election)
    if state != "live" and request.method == "POST":
        notify_flash_for_state(state)
        return redirect(url_for("election_detail", slug=slug))

    candidates = get_election_candidates(election.id)
    existing_votes = Vote.query.filter_by(election_id=election.id, user_id=user.id).order_by(Vote.rank.asc()).all()

    if request.method == "POST":
        csrf_token = request.form.get("csrf_token", "")
        if csrf_token != session.get("csrf_token"):
            abort(400, "Invalid CSRF token")

        if existing_votes:
            flash("You have already voted in this election.", "warning")
            return redirect(url_for("election_detail", slug=slug))

        selected = request.form.getlist("candidate_ids")
        selected_ids = []
        for val in selected:
            try:
                cid = int(val)
                selected_ids.append(cid)
            except ValueError:
                continue

        selected_ids = list(dict.fromkeys(selected_ids))
        if not selected_ids and not election.allow_abstain:
            flash("You must select at least one candidate.", "danger")
            return redirect(url_for("election_detail", slug=slug))

        if len(selected_ids) > election.max_choices:
            flash(f"You can choose at most {election.max_choices} candidate(s).", "danger")
            return redirect(url_for("election_detail", slug=slug))

        candidate_ids = {c.id for c in candidates}
        for cid in selected_ids:
            if cid not in candidate_ids:
                flash("Invalid candidate selected.", "danger")
                return redirect(url_for("election_detail", slug=slug))

        if not selected_ids and election.allow_abstain:
            vote = Vote(
                election_id=election.id,
                user_id=user.id,
                candidate_id=None,
                rank=1,
                vote_token=secrets.token_hex(16),
                client_fingerprint=get_client_fingerprint(),
            )
            db.session.add(vote)
        else:
            for rank, cid in enumerate(selected_ids, start=1):
                vote = Vote(
                    election_id=election.id,
                    user_id=user.id,
                    candidate_id=cid,
                    rank=rank,
                    vote_token=secrets.token_hex(16),
                    client_fingerprint=get_client_fingerprint(),
                )
                db.session.add(vote)

        log_action(
            "cast_vote",
            "election",
            election.id,
            metadata={"selected": selected_ids, "max_choices": election.max_choices},
        )
        db.session.commit()

        flash("Your vote has been securely recorded. Thank you!", "success")
        return redirect(url_for("results", slug=slug))

    voted_candidate_ids = [v.candidate_id for v in existing_votes if v.candidate_id]
    turnout = calculate_turnout(election.id)

    return render_template(
        "election_detail.html",
        election=election,
        candidates=candidates,
        existing_votes=existing_votes,
        voted_candidate_ids=voted_candidate_ids,
        state=state,
        turnout=turnout,
    )


@app.route("/results/<slug>")
@require_login
def results(slug: str):
    user = current_user()
    election = Election.query.filter_by(slug=slug).first_or_404()

    if not is_eligible_for_election(user, election):
        flash("You are not eligible for this private election.", "danger")
        return redirect(url_for("dashboard"))

    state = election_state(election)
    if state == "live" and not election.allow_live_results and not user.is_admin:
        flash("Live results are hidden until voting closes.", "warning")
        return redirect(url_for("election_detail", slug=slug))

    result_bundle = compute_results(election.id)
    turnout = calculate_turnout(election.id)
    history = (
        AuditLog.query.filter_by(target_type="election", target_id=election.id, action="cast_vote")
        .order_by(AuditLog.created_at.desc())
        .limit(8)
        .all()
    )

    trend_points = build_trend_points(election.id)

    return render_template(
        "results.html",
        election=election,
        result_bundle=result_bundle,
        turnout=turnout,
        history=history,
        state=state,
        trend_points=trend_points,
    )


def build_trend_points(election_id: int) -> list[dict]:
    votes = Vote.query.filter_by(election_id=election_id, rank=1).order_by(Vote.created_at.asc()).all()
    if not votes:
        return []
    buckets = defaultdict(int)
    for vote in votes:
        key = vote.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:00")
        buckets[key] += 1
    cumulative = 0
    points = []
    for key in sorted(buckets.keys()):
        cumulative += buckets[key]
        points.append({"bucket": key, "votes": cumulative})
    return points


# -----------------------------------------------------------------------------
# Admin pages
# -----------------------------------------------------------------------------


@app.route("/admin")
@require_admin
def admin_home():
    user = current_user()
    elections = Election.query.order_by(Election.created_at.desc()).limit(40).all()
    logs = summarize_recent_activity(20)

    status_counts = Counter(election_state(e) for e in elections)
    top_elections = []
    for election in elections[:8]:
        turnout = calculate_turnout(election.id)
        top_elections.append(
            {
                "id": election.id,
                "slug": election.slug,
                "title": election.title,
                "state": election_state(election),
                "votes": turnout["votes"],
                "turnout": turnout["turnout"],
            }
        )

    return render_template(
        "admin/home.html",
        admin=user,
        elections=elections,
        logs=logs,
        status_counts=status_counts,
        top_elections=top_elections,
    )


@app.route("/admin/elections/new", methods=["GET", "POST"])
@require_admin
def admin_create_election():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "general").strip()
        visibility = request.form.get("visibility", "public").strip()
        start_at = parse_dt(request.form.get("start_at"))
        end_at = parse_dt(request.form.get("end_at"))
        allow_abstain = bool(request.form.get("allow_abstain"))
        allow_live_results = bool(request.form.get("allow_live_results"))
        max_choices = request.form.get("max_choices", type=int) or 1
        banner = request.form.get("banner", "aurora")
        rules = request.form.get("rules", "").strip()
        candidates_raw = request.form.get("candidates", "")
        invitees_raw = request.form.get("invitees", "")

        if len(title) < 5:
            flash("Election title must be at least 5 characters.", "danger")
            return redirect(url_for("admin_create_election"))
        if not description:
            flash("Description is required.", "danger")
            return redirect(url_for("admin_create_election"))
        if not start_at or not end_at or end_at <= start_at:
            flash("Please provide a valid schedule where end time is after start time.", "danger")
            return redirect(url_for("admin_create_election"))

        candidate_payload = parse_candidates_from_form(candidates_raw)
        if len(candidate_payload) < 2 and not allow_abstain:
            flash("Add at least two candidates (or enable abstain).", "danger")
            return redirect(url_for("admin_create_election"))

        election = Election(
            slug=slugify(title),
            title=title,
            description=description,
            category=category[:80],
            visibility=visibility if visibility in {"public", "private"} else "public",
            status="draft",
            start_at=start_at,
            end_at=end_at,
            allow_abstain=allow_abstain,
            allow_live_results=allow_live_results,
            max_choices=max(1, min(max_choices, 5)),
            banner=banner[:255],
            rules=rules[:8000],
            created_by=current_user().id,
        )
        db.session.add(election)
        db.session.flush()

        for idx, payload in enumerate(candidate_payload, start=1):
            if not payload["name"]:
                continue
            db.session.add(
                Candidate(
                    election_id=election.id,
                    name=payload["name"],
                    slogan=payload["slogan"],
                    manifesto=payload["manifesto"],
                    experience_years=payload["experience_years"],
                    icon=["spark", "leaf", "rocket", "shield", "star"][idx % 5],
                    sort_order=payload["sort_order"],
                )
            )

        invited_count = 0
        if election.visibility == "private":
            for raw in invitees_raw.splitlines():
                email = raw.strip().lower()
                if not email:
                    continue
                invited_user = User.query.filter_by(email=email).first()
                if invited_user:
                    link = ElectionVoter(election_id=election.id, user_id=invited_user.id)
                    db.session.add(link)
                    invited_count += 1

        log_action(
            "create_election",
            "election",
            election.id,
            metadata={
                "title": title,
                "visibility": election.visibility,
                "candidate_count": len(candidate_payload),
                "invitees": invited_count,
            },
        )
        db.session.commit()

        flash("Election created successfully. You can publish it from admin controls.", "success")
        return redirect(url_for("admin_edit_election", election_id=election.id))

    return render_template("admin/election_form.html", mode="create", election=None)


@app.route("/admin/elections/<int:election_id>/edit", methods=["GET", "POST"])
@require_admin
def admin_edit_election(election_id: int):
    election = Election.query.get_or_404(election_id)

    if request.method == "POST":
        action = request.form.get("action", "save")

        if action == "publish":
            if election.status == "draft":
                election.status = "published"
                log_action("publish_election", "election", election.id)
                db.session.commit()
                flash("Election published and visible to voters.", "success")
            else:
                flash("Only draft elections can be published.", "warning")
            return redirect(url_for("admin_edit_election", election_id=election.id))

        if action == "archive":
            election.status = "archived"
            log_action("archive_election", "election", election.id)
            db.session.commit()
            flash("Election archived.", "info")
            return redirect(url_for("admin_edit_election", election_id=election.id))

        election.title = request.form.get("title", election.title).strip()[:240]
        election.description = request.form.get("description", election.description).strip()[:8000]
        election.category = request.form.get("category", election.category).strip()[:80]
        election.visibility = request.form.get("visibility", election.visibility).strip()[:20]
        election.allow_abstain = bool(request.form.get("allow_abstain"))
        election.allow_live_results = bool(request.form.get("allow_live_results"))
        election.max_choices = max(1, min(request.form.get("max_choices", type=int) or election.max_choices, 5))
        election.banner = request.form.get("banner", election.banner).strip()[:255]
        election.rules = request.form.get("rules", election.rules).strip()[:8000]

        parsed_start = parse_dt(request.form.get("start_at"))
        parsed_end = parse_dt(request.form.get("end_at"))
        if parsed_start and parsed_end and parsed_end > parsed_start:
            election.start_at = parsed_start
            election.end_at = parsed_end

        log_action("update_election", "election", election.id)
        db.session.commit()
        flash("Election details updated.", "success")
        return redirect(url_for("admin_edit_election", election_id=election.id))

    candidates = get_election_candidates(election.id)
    voters = (
        db.session.query(User)
        .join(ElectionVoter, ElectionVoter.user_id == User.id)
        .filter(ElectionVoter.election_id == election.id)
        .all()
    )

    return render_template(
        "admin/election_form.html",
        mode="edit",
        election=election,
        candidates=candidates,
        voters=voters,
    )


@app.route("/admin/elections/<int:election_id>/candidates", methods=["POST"])
@require_admin
def admin_add_candidate(election_id: int):
    election = Election.query.get_or_404(election_id)
    name = request.form.get("name", "").strip()
    slogan = request.form.get("slogan", "").strip()
    manifesto = request.form.get("manifesto", "").strip()
    experience_years = request.form.get("experience_years", type=int) or 0

    if len(name) < 2:
        flash("Candidate name is too short.", "danger")
        return redirect(url_for("admin_edit_election", election_id=election.id))

    sort_order = Candidate.query.filter_by(election_id=election.id).count() + 1
    candidate = Candidate(
        election_id=election.id,
        name=name[:150],
        slogan=slogan[:255],
        manifesto=manifesto[:3000],
        experience_years=max(0, experience_years),
        icon="star",
        sort_order=sort_order,
    )
    db.session.add(candidate)
    log_action("add_candidate", "candidate", metadata={"election_id": election.id, "name": name})
    db.session.commit()

    flash("Candidate added.", "success")
    return redirect(url_for("admin_edit_election", election_id=election.id))


@app.route("/admin/candidates/<int:candidate_id>/delete", methods=["POST"])
@require_admin
def admin_delete_candidate(candidate_id: int):
    candidate = Candidate.query.get_or_404(candidate_id)
    election_id = candidate.election_id
    db.session.delete(candidate)
    log_action("delete_candidate", "candidate", candidate_id)
    db.session.commit()
    flash("Candidate removed.", "info")
    return redirect(url_for("admin_edit_election", election_id=election_id))


@app.route("/admin/elections/<int:election_id>/voters", methods=["POST"])
@require_admin
def admin_add_voter(election_id: int):
    election = Election.query.get_or_404(election_id)
    email = request.form.get("email", "").strip().lower()

    user = User.query.filter_by(email=email).first()
    if not user:
        flash("No user found with that email.", "danger")
        return redirect(url_for("admin_edit_election", election_id=election.id))

    exists = ElectionVoter.query.filter_by(election_id=election.id, user_id=user.id).first()
    if exists:
        flash("User is already invited.", "warning")
        return redirect(url_for("admin_edit_election", election_id=election.id))

    db.session.add(ElectionVoter(election_id=election.id, user_id=user.id))
    log_action("invite_voter", "election", election.id, metadata={"user_id": user.id})
    db.session.commit()

    flash(f"{user.name} was added to voter list.", "success")
    return redirect(url_for("admin_edit_election", election_id=election.id))


@app.route("/admin/announcements", methods=["GET", "POST"])
@require_admin
def admin_announcements():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()
        is_published = bool(request.form.get("is_published"))

        if len(title) < 3 or len(body) < 10:
            flash("Announcement title/body are too short.", "danger")
            return redirect(url_for("admin_announcements"))

        ann = Announcement(title=title[:200], body=body[:5000], is_published=is_published, created_by=current_user().id)
        db.session.add(ann)
        log_action("create_announcement", "announcement", metadata={"title": title})
        db.session.commit()
        flash("Announcement posted.", "success")
        return redirect(url_for("admin_announcements"))

    announcements = Announcement.query.order_by(Announcement.created_at.desc()).limit(50).all()
    return render_template("admin/announcements.html", announcements=announcements)


@app.route("/admin/analytics")
@require_admin
def admin_analytics():
    elections = Election.query.all()
    records = []
    for election in elections:
        turnout = calculate_turnout(election.id)
        result = compute_results(election.id)
        records.append(
            {
                "id": election.id,
                "slug": election.slug,
                "title": election.title,
                "state": election_state(election),
                "category": election.category,
                "votes": turnout["votes"],
                "eligible": turnout["eligible"],
                "turnout": turnout["turnout"],
                "winner": result["winner"]["name"] if result["winner"] else "—",
            }
        )

    avg_turnout = round(mean([r["turnout"] for r in records]), 2) if records else 0
    return render_template("admin/analytics.html", records=records, avg_turnout=avg_turnout)


# -----------------------------------------------------------------------------
# JSON APIs
# -----------------------------------------------------------------------------


@app.route("/api/elections")
def api_elections():
    status = request.args.get("status", "all")
    q = request.args.get("q", "")
    category = request.args.get("category", "all")

    query = Election.query.order_by(Election.start_at.desc())
    query = election_filters(query, status, q, category)
    rows = []
    for election in query.limit(100).all():
        rows.append(
            {
                "id": election.id,
                "slug": election.slug,
                "title": election.title,
                "description": election.description,
                "category": election.category,
                "visibility": election.visibility,
                "status": election.status,
                "state": election_state(election),
                "start_at": to_iso(election.start_at),
                "end_at": to_iso(election.end_at),
            }
        )
    return jsonify({"items": rows, "count": len(rows)})


@app.route("/api/elections/<slug>/results")
@require_login
def api_results(slug: str):
    user = current_user()
    election = Election.query.filter_by(slug=slug).first_or_404()
    if not is_eligible_for_election(user, election):
        return jsonify({"error": "not_eligible"}), 403

    if election_state(election) == "live" and not election.allow_live_results and not user.is_admin:
        return jsonify({"error": "results_hidden"}), 403

    payload = compute_results(election.id)
    payload["election"] = {
        "slug": election.slug,
        "title": election.title,
        "state": election_state(election),
    }
    payload["turnout"] = calculate_turnout(election.id)
    return jsonify(payload)


@app.route("/api/admin/metrics")
@require_admin
def api_admin_metrics():
    elections_count = Election.query.count()
    users_count = User.query.count()
    votes_count = Vote.query.filter_by(rank=1).count()
    published_count = Election.query.filter(Election.status == "published").count()

    by_category = (
        db.session.query(Election.category, func.count(Election.id))
        .group_by(Election.category)
        .order_by(func.count(Election.id).desc())
        .all()
    )

    return jsonify(
        {
            "elections": elections_count,
            "users": users_count,
            "votes": votes_count,
            "published": published_count,
            "categories": [{"name": row[0], "count": row[1]} for row in by_category],
        }
    )


# -----------------------------------------------------------------------------
# Demo data + CLI utilities
# -----------------------------------------------------------------------------


@app.route("/admin/seed-demo", methods=["POST"])
@require_admin
def seed_demo_data_route():
    created = seed_demo_data()
    flash(f"Seeded demo data: {created['elections']} election(s), {created['votes']} vote(s).", "success")
    return redirect(url_for("admin_home"))


def seed_demo_data() -> dict:
    if Election.query.count() > 0:
        return {"elections": 0, "votes": 0}

    admin = User.query.filter_by(is_admin=True).first()
    if not admin:
        return {"elections": 0, "votes": 0}

    users = User.query.limit(8).all()
    if len(users) < 6:
        starter = [
            ("Ava Stone", "ava@example.com"),
            ("Noah Cruz", "noah@example.com"),
            ("Mia Park", "mia@example.com"),
            ("Leo Patel", "leo@example.com"),
            ("Ivy Smith", "ivy@example.com"),
            ("Owen Gray", "owen@example.com"),
            ("Luna Reed", "luna@example.com"),
            ("Ethan Cole", "ethan@example.com"),
        ]
        for name, email in starter:
            if not User.query.filter_by(email=email).first():
                db.session.add(User(name=name, email=email, password_hash=generate_password_hash("password123")))
        db.session.commit()
        users = User.query.limit(12).all()

    now = utcnow()
    elections_data = [
        {
            "title": "Global Community Leadership 2026",
            "description": "Vote for the next global community leadership direction.",
            "category": "governance",
            "start": now - timedelta(days=2),
            "end": now + timedelta(days=3),
            "candidates": [
                ("Avery Johnson", "Build trust through transparent governance", 11),
                ("Priya Nair", "Ship measurable outcomes every quarter", 9),
                ("Daniel Kim", "Invest in education and equity", 7),
            ],
        },
        {
            "title": "Innovation Committee Chair",
            "description": "Select the committee chair focused on AI and open innovation.",
            "category": "technology",
            "start": now + timedelta(days=1),
            "end": now + timedelta(days=4),
            "candidates": [
                ("Sara Mitchell", "Experiment faster, learn faster", 8),
                ("Liam Rodgers", "Scale prototypes to production", 10),
            ],
        },
        {
            "title": "Cultural Festival Theme 2026",
            "description": "Choose the annual cultural festival theme.",
            "category": "culture",
            "start": now - timedelta(days=10),
            "end": now - timedelta(days=1),
            "candidates": [
                ("Future Folklore", "Blend heritage and modern creativity", 1),
                ("World Mosaic", "Celebrate our diverse global stories", 1),
                ("Neon Traditions", "A vibrant tech-art crossover", 1),
            ],
        },
    ]

    created_elections = 0
    created_votes = 0

    for payload in elections_data:
        election = Election(
            slug=slugify(payload["title"]),
            title=payload["title"],
            description=payload["description"],
            category=payload["category"],
            visibility="public",
            status="published",
            start_at=payload["start"],
            end_at=payload["end"],
            allow_abstain=False,
            allow_live_results=True,
            max_choices=1,
            banner="aurora",
            rules="Be respectful. One person, one ballot.",
            created_by=admin.id,
        )
        db.session.add(election)
        db.session.flush()

        candidates = []
        for idx, (name, slogan, exp) in enumerate(payload["candidates"], start=1):
            c = Candidate(
                election_id=election.id,
                name=name,
                slogan=slogan,
                manifesto=f"{name} believes in practical, people-first policy backed by transparent metrics.",
                experience_years=exp,
                icon="star",
                sort_order=idx,
            )
            db.session.add(c)
            db.session.flush()
            candidates.append(c)

        if election_state(election) in {"live", "ended"}:
            for u in users[: min(7, len(users))]:
                selected = candidates[(u.id + election.id) % len(candidates)]
                v = Vote(
                    election_id=election.id,
                    user_id=u.id,
                    candidate_id=selected.id,
                    rank=1,
                    vote_token=secrets.token_hex(16),
                    client_fingerprint=f"seed::{u.id}",
                )
                db.session.add(v)
                created_votes += 1

        created_elections += 1

    log_action("seed_demo_data", "system", metadata={"elections": created_elections, "votes": created_votes})
    db.session.commit()
    return {"elections": created_elections, "votes": created_votes}


def bootstrap_admin() -> None:
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_password = os.getenv("ADMIN_PASSWORD", "admin12345")
    if not User.query.filter_by(email=admin_email).first():
        admin = User(
            name="Platform Admin",
            email=admin_email,
            password_hash=generate_password_hash(admin_password),
            is_admin=True,
            bio="System administrator account.",
            timezone="UTC",
        )
        db.session.add(admin)
        db.session.commit()


@app.errorhandler(400)
def bad_request(e):
    return render_template("errors/error.html", code=400, message=str(e)), 400


@app.errorhandler(403)
def forbidden(e):
    return render_template("errors/error.html", code=403, message="Access denied"), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("errors/error.html", code=404, message="Page not found"), 404


@app.errorhandler(413)
def payload_too_large(e):
    return render_template("errors/error.html", code=413, message="Payload too large"), 413


@app.errorhandler(500)
def server_error(e):
    return render_template("errors/error.html", code=500, message="Unexpected server error"), 500


def init_database():
    db.create_all()
    bootstrap_admin()


if __name__ == "__main__":
    with app.app_context():
        init_database()
        if os.getenv("SEED_DEMO", "1") == "1":
            seed_demo_data()
    app.run(host="0.0.0.0", port=5000, debug=True)
