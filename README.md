# NovaVote — Advanced Online Voting Platform

NovaVote is a modern Flask + SQLite online voting application with a premium UI, powerful election lifecycle controls, and rich analytics.

## Highlights

- Secure account registration/login with hashed passwords.
- Admin control center with:
  - Election creation/editing/publishing/archiving.
  - Candidate management (manifesto, slogan, experience).
  - Private voter invitation lists for restricted elections.
  - Announcement publishing.
  - Platform analytics dashboards.
- Voter features:
  - Election browsing dashboard with filters.
  - Ranked-like multiple-choice ballots (`max_choices`).
  - Abstain option support.
  - Results pages with trend chart + turnout metrics.
  - Profile page with recent vote history.
- API endpoints for elections, results, and admin metrics.
- Audit logging for key platform actions.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install flask flask_sqlalchemy werkzeug
python app.py
```

Open: http://127.0.0.1:5000

Default admin account:
- Email: `admin@example.com`
- Password: `admin12345`

## Environment Variables

- `SECRET_KEY` — Flask secret key.
- `DATABASE_URL` — SQLAlchemy DB URI (default SQLite local file).
- `ADMIN_EMAIL` — Bootstrap admin email.
- `ADMIN_PASSWORD` — Bootstrap admin password.
- `SEED_DEMO` — `1` to seed demo data on startup.

## Project Structure

- `app.py` — Full application logic, models, routes, and API.
- `templates/` — Jinja templates for public, voter, and admin pages.
- `static/styles.css` — Visual system and utility classes.
- `static/app.js` — Interactive UI behaviors + chart rendering.

