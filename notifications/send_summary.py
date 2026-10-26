"""Email a plain-text summary of inventory changes to the ESB inbox.

Usage:
    python -m notifications.send_summary scrape_diff.json            # send
    python -m notifications.send_summary scrape_diff.json --dry-run  # print only

SMTP settings come from environment variables (GitHub Actions secrets in CI):
    SMTP_HOST, SMTP_USER, SMTP_PASS, SMTP_FROM   required
    SMTP_PORT                                    optional, default 587 (STARTTLS);
                                                 465 uses implicit TLS
    SMTP_TO                                      optional, default info@eagleshedsidaho.com

Does nothing (exit 0) when nothing was added, removed, or repriced.
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import sys
from collections.abc import Mapping
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

DEFAULT_TO = "info@eagleshedsidaho.com"
DEFAULT_PORT = 587
REQUIRED_ENV = ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "SMTP_FROM")
SMTP_TIMEOUT = 30

PRICE_LABELS = {
    "cash_price_usd": "Cash",
    "rto_36mo_usd": "36-mo RTO",
    "rto_60mo_usd": "60-mo RTO",
    "rto_84mo_usd": "84-mo RTO",
}


class ConfigError(RuntimeError):
    pass


def has_changes(diff: Mapping) -> bool:
    return bool(diff.get("added") or diff.get("removed") or diff.get("price_changed"))


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def _title(shed: Mapping) -> str:
    return f"{shed.get('style')} {shed.get('width_ft')}x{shed.get('length_ft')}"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def subject_line(diff: Mapping) -> str:
    parts = []
    if diff.get("added"):
        parts.append(f"{len(diff['added'])} added")
    if diff.get("removed"):
        parts.append(f"{len(diff['removed'])} sold/removed")
    if diff.get("price_changed"):
        parts.append(_plural(len(diff["price_changed"]), "price change"))
    return "Eagle lot inventory: " + ", ".join(parts)


def body_text(diff: Mapping) -> str:
    stamp = diff.get("generated_at")
    when = datetime.fromisoformat(stamp).strftime("%b %d, %Y") if stamp else "today"
    lines = [
        f"Changes to the Old Hickory inventory at the Eagle lot, checked {when}.",
        "",
    ]

    if diff.get("added"):
        lines.append(f"NEW ON THE LOT ({len(diff['added'])})")
        for shed in diff["added"]:
            lines.append(f"  - {_title(shed)}: {_money(shed.get('cash_price_usd'))} cash")
            lines.append(f"    ID {shed.get('inventory_id')}  {shed.get('oh_detail_url') or ''}")
        lines.append("")

    if diff.get("removed"):
        lines.append(f"SOLD OR REMOVED ({len(diff['removed'])})")
        for shed in diff["removed"]:
            lines.append(f"  - {_title(shed)}: was {_money(shed.get('cash_price_usd'))} cash")
            lines.append(f"    ID {shed.get('inventory_id')}")
        lines.append("")

    if diff.get("price_changed"):
        lines.append(f"PRICE CHANGES ({len(diff['price_changed'])})")
        for shed in diff["price_changed"]:
            lines.append(f"  - {_title(shed)}  (ID {shed.get('inventory_id')})")
            for field, change in shed.get("changes", {}).items():
                old, new = change.get("old"), change.get("new")
                delta = ""
                if old is not None and new is not None:
                    sign = "+" if new > old else "-"
                    delta = f"  ({sign}{_money(abs(new - old))})"
                label = PRICE_LABELS.get(field, field)
                lines.append(f"      {label}: {_money(old)} -> {_money(new)}{delta}")
        lines.append("")

    lines += [
        "The website widget has already been updated with these changes.",
        f"Source: {diff.get('source_url') or 'oldhickorybuildings.com'}",
        "",
        "-- ESB Inventory Mirror (automated nightly check)",
    ]
    return "\n".join(lines) + "\n"


def build_message(diff: Mapping, sender: str, recipient: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject_line(diff)
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(body_text(diff))
    return msg


def smtp_settings(env: Mapping[str, str]) -> dict:
    missing = [name for name in REQUIRED_ENV if not env.get(name)]
    if missing:
        raise ConfigError(f"missing environment variables: {', '.join(missing)}")
    try:
        port = int(env.get("SMTP_PORT") or DEFAULT_PORT)
    except ValueError as exc:
        raise ConfigError("SMTP_PORT must be a number") from exc
    return {
        "host": env["SMTP_HOST"],
        "port": port,
        "user": env["SMTP_USER"],
        "password": env["SMTP_PASS"],
        "sender": env["SMTP_FROM"],
        "recipient": env.get("SMTP_TO") or DEFAULT_TO,
    }


def send(msg: EmailMessage, settings: Mapping) -> None:
    context = ssl.create_default_context()
    if settings["port"] == 465:
        server = smtplib.SMTP_SSL(
            settings["host"], settings["port"], timeout=SMTP_TIMEOUT, context=context
        )
    else:
        server = smtplib.SMTP(settings["host"], settings["port"], timeout=SMTP_TIMEOUT)
    with server:
        if settings["port"] != 465:
            server.starttls(context=context)
        server.login(settings["user"], settings["password"])
        server.send_message(msg)


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("diff_file", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="print the email instead")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env

    diff = json.loads(args.diff_file.read_text(encoding="utf-8"))
    if not has_changes(diff):
        print("No added, removed, or repriced sheds; no email sent.")
        return 0

    if args.dry_run:
        msg = build_message(
            diff, env.get("SMTP_FROM") or "(SMTP_FROM)", env.get("SMTP_TO") or DEFAULT_TO
        )
        print(msg.as_string())
        return 0

    try:
        settings = smtp_settings(env)
        msg = build_message(diff, settings["sender"], settings["recipient"])
        send(msg, settings)
    except ConfigError as exc:
        print(f"send_summary: {exc}", file=sys.stderr)
        return 1
    except (smtplib.SMTPException, OSError) as exc:
        # Never echo credentials; the exception text from smtplib doesn't include them.
        print(f"send_summary: could not send email: {exc}", file=sys.stderr)
        return 1
    print(f"Sent '{msg['Subject']}' to {settings['recipient']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
