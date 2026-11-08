import concurrent.futures
import sqlite3

import pytest
from fastapi.testclient import TestClient

from harvey import dashboard
from harvey.account_auth import AccountError, COOKIE_NAME, LocalAccounts

ORIGIN = "http://127.0.0.1:5555"
EMAIL = "owner@example.test"
PASSWORD = "a-long-test-password"


@pytest.fixture
def client():
    with TestClient(dashboard.app, base_url=ORIGIN) as client:
        yield client


def signup(client):
    return client.post("/api/auth/signup", json={
        "email": EMAIL, "password": PASSWORD, "confirm_password": PASSWORD,
    })


def test_account_creation_login_and_revocable_session(client):
    response = signup(client)
    assert response.status_code == 201
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    token = client.cookies.get(COOKIE_NAME)
    assert client.get("/api/companies").status_code == 200
    assert client.get("/api/auth/session").json()["user"]["email"] == EMAIL
    assert client.get("/").status_code == 200
    assert "__DASHBOARD_SESSION__" not in client.get("/").text

    accounts = dashboard._accounts()
    assert LocalAccounts(accounts.path).user(token) == {"email": EMAIL}
    contents = accounts.path.read_bytes()
    assert PASSWORD.encode() not in contents and token.encode() not in contents

    assert client.post("/api/auth/logout").status_code == 200
    assert accounts.user(token) is None
    assert client.get("/api/companies").status_code == 401
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    # The legacy API token cannot bypass the owner account.
    legacy = dashboard.token_store.get()
    assert client.get("/api/companies", headers={"Authorization": f"Bearer {legacy}"}).status_code == 401
    response = client.post("/api/auth/login", json={"email": EMAIL.upper(), "password": PASSWORD})
    assert response.status_code == 200
    assert client.get("/api/companies").status_code == 200


def test_a_second_signup_cannot_replace_the_owner(client):
    assert signup(client).status_code == 201
    assert signup(client).status_code == 409
    assert client.get("/api/auth/session").json()["user"] == {"email": EMAIL}


def test_invalid_credentials_and_password_confirmation(client):
    assert client.post("/api/auth/signup", json={
        "email": EMAIL, "password": PASSWORD, "confirm_password": "different",
    }).status_code == 400
    assert not dashboard._accounts().has_owner()
    assert client.post("/api/auth/signup", json={
        "email": "not-an-email", "password": PASSWORD, "confirm_password": PASSWORD,
    }).status_code == 422
    assert signup(client).status_code == 201
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": EMAIL, "password": "wrong-password"}).status_code == 401
    assert client.get("/api/auth/session").json()["user"] is None


def test_expired_sessions_and_login_throttling(client):
    assert signup(client).status_code == 201
    with sqlite3.connect(dashboard._accounts().path) as db:
        db.execute("UPDATE sessions SET expires_at = 0")
    assert client.get("/api/companies").status_code == 401
    for _ in range(10):
        assert client.post("/api/auth/login", json={"email": EMAIL, "password": "wrong-password"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 429


def test_login_and_signup_reject_cross_origin_and_non_json(client):
    for endpoint in ("signup", "login", "logout"):
        response = client.post(f"/api/auth/{endpoint}", headers={"Origin": "https://other.example"}, json={})
        assert response.status_code == 403
        assert client.post(f"/api/auth/{endpoint}", content="email=x", headers={"Content-Type": "text/plain"}).status_code == 415


def test_concurrent_signups_create_only_one_owner(tmp_path):
    accounts = LocalAccounts(tmp_path / "accounts.db")

    def create(index):
        try:
            accounts.signup(f"owner{index}@example.test", PASSWORD, f"client{index}")
            return 201
        except AccountError as error:
            return error.status

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(create, range(2))) == [201, 409]
