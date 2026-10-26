from __future__ import annotations

import json
import smtplib
from typing import ClassVar

import pytest

from notifications import send_summary

ENV = {
    "SMTP_HOST": "smtp.gmail.com",
    "SMTP_USER": "bot@eagleshedsidaho.com",
    "SMTP_PASS": "app-password-123",
    "SMTP_FROM": "ESB Inventory <bot@eagleshedsidaho.com>",
}

DIFF = {
    "added": [
        {
            "inventory_id": "newshed1",
            "style": "Lofted Barn",
            "width_ft": 10,
            "length_ft": 20,
            "cash_price_usd": 7480.0,
            "oh_detail_url": "https://oldhickorybuildings.com/product/newshed1/",
        }
    ],
    "removed": [
        {
            "inventory_id": "soldshed",
            "style": "Utility",
            "width_ft": 10,
            "length_ft": 12,
            "cash_price_usd": 4056.0,
            "oh_detail_url": "https://oldhickorybuildings.com/product/soldshed/",
        }
    ],
    "price_changed": [
        {
            "inventory_id": "01tjw00000j6kkwia3",
            "style": "Barn",
            "width_ft": 8,
            "length_ft": 12,
            "cash_price_usd": 3140.75,
            "changes": {
                "cash_price_usd": {"old": 3500.0, "new": 3140.75},
                "rto_36mo_usd": {"old": 150.0, "new": 145.41},
            },
        }
    ],
    "content_changed": True,
    "generated_at": "2026-10-27T12:00:00+00:00",
    "source_url": "https://oldhickorybuildings.com/locations/eagle-idaho/",
}

EMPTY = {"added": [], "removed": [], "price_changed": [], "content_changed": True}


class FakeSMTP:
    instances: ClassVar[list[FakeSMTP]] = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port
        self.calls: list[str] = []
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append("quit")

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(f"login:{user}")

    def send_message(self, msg):
        self.calls.append("send")
        self.sent.append(msg)


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


@pytest.fixture
def diff_file(tmp_path):
    path = tmp_path / "diff.json"
    path.write_text(json.dumps(DIFF), encoding="utf-8")
    return path


def test_subject_summarizes_counts():
    assert send_summary.subject_line(DIFF) == (
        "Eagle lot inventory: 1 added, 1 sold/removed, 1 price change"
    )


def test_body_lists_every_change():
    body = send_summary.body_text(DIFF)
    assert "checked Oct 27, 2026" in body
    assert "NEW ON THE LOT (1)" in body
    assert "Lofted Barn 10x20: $7,480.00 cash" in body
    assert "https://oldhickorybuildings.com/product/newshed1/" in body
    assert "SOLD OR REMOVED (1)" in body
    assert "Utility 10x12: was $4,056.00 cash" in body
    assert "PRICE CHANGES (1)" in body
    assert "Cash: $3,500.00 -> $3,140.75  (-$359.25)" in body
    assert "36-mo RTO: $150.00 -> $145.41  (-$4.59)" in body


def test_body_omits_empty_sections():
    body = send_summary.body_text({**DIFF, "removed": [], "price_changed": []})
    assert "SOLD OR REMOVED" not in body
    assert "PRICE CHANGES" not in body


def test_message_is_plain_text():
    msg = send_summary.build_message(DIFF, ENV["SMTP_FROM"], "info@eagleshedsidaho.com")
    assert msg.get_content_type() == "text/plain"
    assert msg["To"] == "info@eagleshedsidaho.com"


def test_sends_with_starttls_by_default(fake_smtp, diff_file, capsys):
    assert send_summary.main([str(diff_file)], env=ENV) == 0
    (server,) = fake_smtp.instances
    assert (server.host, server.port) == ("smtp.gmail.com", 587)
    assert server.calls == ["starttls", f"login:{ENV['SMTP_USER']}", "send", "quit"]
    (msg,) = server.sent
    assert msg["To"] == "info@eagleshedsidaho.com"
    assert ENV["SMTP_PASS"] not in capsys.readouterr().out


def test_port_465_uses_implicit_tls(fake_smtp, diff_file):
    assert send_summary.main([str(diff_file)], env={**ENV, "SMTP_PORT": "465"}) == 0
    assert "starttls" not in fake_smtp.instances[0].calls


def test_smtp_to_override(fake_smtp, diff_file):
    send_summary.main([str(diff_file)], env={**ENV, "SMTP_TO": "nikki@example.com"})
    assert fake_smtp.instances[0].sent[0]["To"] == "nikki@example.com"


def test_no_changes_sends_nothing(fake_smtp, tmp_path):
    path = tmp_path / "diff.json"
    path.write_text(json.dumps(EMPTY), encoding="utf-8")
    assert send_summary.main([str(path)], env={}) == 0
    assert fake_smtp.instances == []


def test_missing_secrets_fail_without_leaking(fake_smtp, diff_file, capsys):
    env = {k: v for k, v in ENV.items() if k != "SMTP_PASS"}
    assert send_summary.main([str(diff_file)], env=env) == 1
    err = capsys.readouterr().err
    assert "SMTP_PASS" in err
    assert ENV["SMTP_USER"] not in err
    assert fake_smtp.instances == []


def test_smtp_error_returns_failure(monkeypatch, diff_file):
    def boom(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    assert send_summary.main([str(diff_file)], env=ENV) == 1


def test_dry_run_prints_without_sending(fake_smtp, diff_file, capsys):
    assert send_summary.main([str(diff_file), "--dry-run"], env={}) == 0
    assert "Subject: Eagle lot inventory" in capsys.readouterr().out
    assert fake_smtp.instances == []
