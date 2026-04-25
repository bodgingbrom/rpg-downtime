"""Tests for the v2 admin-gating helper (checks.author_has_role)."""
from __future__ import annotations

from types import SimpleNamespace

import checks


def _ctx_with_roles(*role_names: str):
    """Build a minimal commands.Context-like object with a roles attribute."""
    roles = [SimpleNamespace(name=n) for n in role_names]
    author = SimpleNamespace(roles=roles)
    return SimpleNamespace(author=author)


def test_author_has_role_returns_true_when_no_role_required():
    """A None / empty min_role means 'no gate' — always allowed."""
    ctx = _ctx_with_roles("Some Role")
    assert checks.author_has_role(ctx, None) is True
    assert checks.author_has_role(ctx, "") is True


def test_author_has_role_true_when_role_present():
    ctx = _ctx_with_roles("Race Admin", "Member")
    assert checks.author_has_role(ctx, "Race Admin") is True


def test_author_has_role_false_when_role_missing():
    ctx = _ctx_with_roles("Member")
    assert checks.author_has_role(ctx, "Race Admin") is False


def test_author_has_role_false_when_no_roles_at_all():
    ctx = SimpleNamespace(author=SimpleNamespace(roles=[]))
    assert checks.author_has_role(ctx, "Race Admin") is False


def test_author_has_role_false_when_ctx_is_none():
    """Defensive: a None context with a real role requirement is denied."""
    assert checks.author_has_role(None, "Race Admin") is False


def test_author_has_role_true_when_ctx_is_none_and_no_role_required():
    """A None context is fine if no gate exists."""
    assert checks.author_has_role(None, None) is True
