# Online Voting Functional Project

A complete starter online voting system built with Flask + SQLite.

## Features
- User registration and login.
- Admin bootstrapping and election creation.
- Create elections with multiple candidates.
- One-person-one-vote per election enforcement.
- Election result dashboard with percentages.
- Basic automated tests with `pytest`.

## Default Admin
The app auto-creates an admin account on first run:
- Email: `admin@example.com`
- Password: `admin123`

You can override with environment variables:
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`

## Run Locally
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open: `http://localhost:5000`

## Run Tests
```bash
pytest -q
```
