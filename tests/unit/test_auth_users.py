"""Tests for auth_users pure + file-backed logic (no bot needed)."""

import json
import auth_users
from pathlib import Path


def test_is_authorized_with_file(tmp_path):
    users_file = tmp_path / "authorized.json"
    users_file.write_text(
        json.dumps({"users": {"123456": {"id": 123456, "username": "vip1"}}})
    )
    auth_users.configure(users_file=str(users_file), max_users=5)

    assert auth_users.is_authorized(123456) is True
    assert auth_users.is_authorized(999999) is False


def test_add_and_remove_user(tmp_path):
    users_file = tmp_path / "authorized.json"
    auth_users.configure(users_file=str(users_file), max_users=2, seed_user_ids=[])

    assert auth_users.add_user(42, "test", "T") == "ok"
    assert auth_users.is_authorized(42) is True

    assert auth_users.remove_user(42) is True
    assert auth_users.is_authorized(42) is False


def test_max_users_limit(tmp_path):
    users_file = tmp_path / "authorized.json"
    auth_users.configure(users_file=str(users_file), max_users=1, seed_user_ids=[])

    assert auth_users.add_user(1, None, None) == "ok"
    assert auth_users.add_user(2, None, None) == "full"
