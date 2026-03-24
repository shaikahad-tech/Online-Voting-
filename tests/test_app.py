import os
import sys
import tempfile

import pytest
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import app, db, User, Election, Candidate, Vote


@pytest.fixture
def client():
    db_fd, db_path = tempfile.mkstemp()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"

    with app.app_context():
        db.drop_all()
        db.create_all()
        admin = User(
            name="Admin",
            email="admin@test.com",
            password_hash=generate_password_hash("adminpass"),
            is_admin=True,
        )
        db.session.add(admin)
        db.session.commit()

    with app.test_client() as client:
        yield client


def register(client, name="Alice", email="alice@test.com", password="secret"):
    return client.post("/register", data={"name": name, "email": email, "password": password}, follow_redirects=True)


def login(client, email="alice@test.com", password="secret"):
    return client.post("/login", data={"email": email, "password": password}, follow_redirects=True)


def test_register_and_login(client):
    response = register(client)
    assert b"Registration successful" in response.data

    response = login(client)
    assert b"Logged in successfully" in response.data


def test_create_election_and_vote_flow(client):
    register(client)
    login(client)

    with app.app_context():
        election = Election(title="City Mayor", description="Vote for mayor")
        db.session.add(election)
        db.session.flush()
        c1 = Candidate(name="Candidate A", election_id=election.id)
        c2 = Candidate(name="Candidate B", election_id=election.id)
        db.session.add_all([c1, c2])
        db.session.commit()

    response = client.post("/election/1", data={"candidate_id": 1}, follow_redirects=True)
    assert b"Vote submitted successfully" in response.data

    with app.app_context():
        assert Vote.query.count() == 1


def test_results_aggregation(client):
    with app.app_context():
        election = Election(title="Best Food", description="Vote for best food")
        db.session.add(election)
        db.session.flush()
        election_id = election.id

        c1 = Candidate(name="Pizza", election_id=election.id)
        c2 = Candidate(name="Sushi", election_id=election.id)
        db.session.add_all([c1, c2])

        u1 = User(name="User1", email="u1@test.com", password_hash=generate_password_hash("pw1"))
        u2 = User(name="User2", email="u2@test.com", password_hash=generate_password_hash("pw2"))
        u3 = User(name="User3", email="u3@test.com", password_hash=generate_password_hash("pw3"))
        db.session.add_all([u1, u2, u3])
        db.session.flush()

        db.session.add_all(
            [
                Vote(user_id=u1.id, election_id=election.id, candidate_id=c1.id),
                Vote(user_id=u2.id, election_id=election.id, candidate_id=c1.id),
                Vote(user_id=u3.id, election_id=election.id, candidate_id=c2.id),
            ]
        )
        db.session.commit()

    response = client.get(f"/results/{election_id}")
    assert response.status_code == 200
    assert b"Total Votes: 3" in response.data
    assert b"Pizza" in response.data and b"Sushi" in response.data
    assert b"66.7%" in response.data and b"33.3%" in response.data
    assert response.data.index(b"Pizza") < response.data.index(b"Sushi")


def test_results_with_no_votes(client):
    with app.app_context():
        election = Election(title="Empty Election", description="No votes yet")
        db.session.add(election)
        db.session.flush()
        election_id = election.id

        db.session.add_all(
            [
                Candidate(name="Option A", election_id=election.id),
                Candidate(name="Option B", election_id=election.id),
            ]
        )
        db.session.commit()

    response = client.get(f"/results/{election_id}")
    assert response.status_code == 200
    assert b"Total Votes: 0" in response.data
    assert b"0.0%" in response.data
