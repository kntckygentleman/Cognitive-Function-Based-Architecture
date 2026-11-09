#!/usr/bin/env python3
"""Beeper Desktop SMS/RCS Triage & Gated Reply CLI for Claire.

Enforces hard operational rules:
1. Read-only triage (`triage`, `search`) and native follow-up reminders (`remind`) run on demand.
2. Outbound SMS/RCS replies CANNOT be sent directly in a single step. Every reply must first be
   staged via `stage-reply` (generating an approval_id and printing the recipient + body) and
   can only be transmitted via `send-staged --approval-id <ID>` after explicit operator sign-off.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse
import urllib.request

def _resolve_workspace_root() -> Path:
    env_r = os.environ.get("CLAIRE_WORKSPACE_ROOT")
    if env_r:
        return Path(env_r).resolve()
    sp = Path(__file__).resolve()
    if len(sp.parents) >= 4 and sp.parents[2].name == "skills" and sp.parents[3].name == ".agents":
        return sp.parents[4]
    return sp.parents[4] if len(sp.parents) >= 4 else Path.cwd()


WORKSPACE_ROOT = _resolve_workspace_root()
_SCRIPTS_DIR = WORKSPACE_ROOT / ".agents" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from claire_hooks import lint_markdown_slop, lint_security_payload  # noqa: E402

ENV_PATH = WORKSPACE_ROOT / ".env"
MCP_CONFIG_PATH = Path.home() / ".gemini" / "config" / "mcp_config.json"
MANIFEST_PATH = WORKSPACE_ROOT / "state" / "epistemic_manifest.json"
DEFAULT_BASE_URL = "http://127.0.0.1:23373"
VOICE_SAMPLE_TTL_SECONDS = 24 * 3600


def load_token() -> str:
    token = os.environ.get("BEEPER_ACCESS_TOKEN", "").strip()
    if token:
        return token

    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("BEEPER_ACCESS_TOKEN="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val

    if MCP_CONFIG_PATH.exists():
        try:
            cfg = json.loads(MCP_CONFIG_PATH.read_text(encoding="utf-8"))
            auth = (
                cfg.get("mcpServers", {})
                .get("beeper", {})
                .get("headers", {})
                .get("Authorization", "")
            )
            if auth.startswith("Bearer "):
                return auth.removeprefix("Bearer ").strip()
        except Exception:
            pass

    sys.stderr.write(
        "Error: BEEPER_ACCESS_TOKEN not found. Open Beeper Desktop -> Settings -> Integrations, "
        "click '+' next to Approved connections, and run:\n"
        "  python3 .agents/skills/beeper-sms-triage/scripts/beeper_triage.py configure-token --token <TOKEN>\n"
    )
    sys.exit(1)


def get_client():
    from beeper_desktop_api import BeeperDesktop

    token = load_token()
    return BeeperDesktop(access_token=token, base_url=DEFAULT_BASE_URL)


def cmd_configure_token(args: argparse.Namespace) -> None:
    token = args.token.strip()
    if not token:
        sys.stderr.write("Error: token cannot be empty.\n")
        sys.exit(1)

    lines: list[str] = []
    if ENV_PATH.exists():
        lines = [
            ln
            for ln in ENV_PATH.read_text(encoding="utf-8").splitlines()
            if not ln.strip().startswith("BEEPER_ACCESS_TOKEN=")
        ]
    lines.append(f'BEEPER_ACCESS_TOKEN="{token}"')
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(ENV_PATH, 0o600)

    MCP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    mcp_data: dict[str, Any] = {"mcpServers": {}}
    if MCP_CONFIG_PATH.exists():
        try:
            mcp_data = json.loads(MCP_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            mcp_data = {"mcpServers": {}}

    mcp_data.setdefault("mcpServers", {})["beeper"] = {
        "url": f"{DEFAULT_BASE_URL}/v0/mcp",
        "headers": {"Authorization": f"Bearer {token}"},
    }
    MCP_CONFIG_PATH.write_text(json.dumps(mcp_data, indent=2) + "\n", encoding="utf-8")
    print(f"Saved BEEPER_ACCESS_TOKEN to {ENV_PATH} and registered 'beeper' MCP in {MCP_CONFIG_PATH}.")

    os.environ["BEEPER_ACCESS_TOKEN"] = token
    try:
        client = get_client()
        accounts = client.accounts.list()
        print("Connected accounts in Beeper Desktop:")
        for acct in accounts:
            print(f"  - account_id={acct.account_id} | network={acct.network}")
    except Exception as exc:
        print(f"Token saved, but live API check returned: {exc}")


def cmd_triage(args: argparse.Namespace) -> None:
    client = get_client()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    cutoff_z = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    search_kwargs: dict[str, Any] = {
        "inbox": "primary",
        "include_muted": False,
        "limit": args.limit,
        "last_activity_after": cutoff_z,
    }
    if args.unread_only:
        search_kwargs["unread_only"] = True
    if args.account_id:
        search_kwargs["account_ids"] = [args.account_id]

    chats_page = client.chats.search(**search_kwargs)
    results = []

    for chat in chats_page.items:
        network_lower = (chat.network or "").lower()
        acct_lower = (chat.account_id or "").lower()
        if args.sms_only and not any(
            k in network_lower or k in acct_lower
            for k in ("google", "sms", "rcs", "android", "messages", "gmessages")
        ):
            continue

        title_str = (chat.title or "").strip()
        is_shortcode = title_str.replace("-", "").isdigit() and len(title_str.replace("-", "")) <= 8
        if not args.include_shortcodes and is_shortcode:
            if getattr(args, "auto_archive_shortcodes", True):
                try:
                    client.chats.archive(chat_id=chat.id, archived=True)
                except Exception:
                    pass
            continue

        msgs_page = client.messages.list(chat.id)
        recent_msgs = []
        for m in list(msgs_page.items)[: args.messages_per_chat]:
            atts = getattr(m, "attachments", None) or []
            att_summary = [
                {"type": getattr(a, "type", None), "file_name": getattr(a, "file_name", None), "mime_type": getattr(a, "mime_type", None)}
                for a in atts
            ]
            recent_msgs.append(
                {
                    "id": m.id,
                    "timestamp": m.timestamp.isoformat() if hasattr(m.timestamp, "isoformat") else str(m.timestamp),
                    "sender_name": getattr(m, "sender_name", None) or m.sender_id,
                    "is_sender": getattr(m, "is_sender", False),
                    "is_unread": getattr(m, "is_unread", False),
                    "text": getattr(m, "text", ""),
                    "attachments": att_summary if att_summary else None,
                }
            )

        results.append(
            {
                "chat_id": chat.id,
                "title": chat.title,
                "network": chat.network,
                "account_id": chat.account_id,
                "type": chat.type,
                "unread_count": chat.unread_count,
                "messages": recent_msgs,
            }
        )

    print(json.dumps({"cutoff_utc": cutoff.isoformat(), "chat_count": len(results), "chats": results}, indent=2))


def cmd_search(args: argparse.Namespace) -> None:
    client = get_client()
    page = client.messages.search(query=args.query, limit=args.limit, include_muted=False)
    items = []
    for m in page.items:
        items.append(
            {
                "id": m.id,
                "chat_id": m.chat_id,
                "account_id": m.account_id,
                "timestamp": m.timestamp.isoformat() if hasattr(m.timestamp, "isoformat") else str(m.timestamp),
                "sender_name": getattr(m, "sender_name", None) or m.sender_id,
                "is_sender": getattr(m, "is_sender", False),
                "text": getattr(m, "text", ""),
            }
        )
    print(json.dumps({"query": args.query, "count": len(items), "messages": items}, indent=2))


def cmd_clean_inbox(args: argparse.Namespace) -> None:
    """Archive only automated 5-to-8-digit shortcode threads; never touch 10-digit or named human threads."""
    client = get_client()
    chats_iter = client.chats.search(inbox="primary", include_muted=True, limit=min(args.limit, 200))
    archived = []
    candidates = []

    scanned = 0
    for chat in chats_iter:
        scanned += 1
        if scanned > args.limit:
            break
        title_str = (chat.title or "").strip()
        clean_digits = title_str.replace("-", "").replace(" ", "")
        is_shortcode = clean_digits.isdigit() and len(clean_digits) <= 8
        if not is_shortcode:
            continue

        preview_text = ""
        if args.inspect_messages:
            try:
                msgs = list(client.messages.list(chat.id).items)[:1]
                if msgs:
                    preview_text = (getattr(msgs[0], "text", "") or "")[:100].replace("\n", " ")
            except Exception:
                pass

        item = {
            "chat_id": chat.id,
            "title": title_str,
            "reason": "shortcode",
            "unread_count": chat.unread_count,
            "preview": preview_text,
        }
        if args.dry_run:
            candidates.append(item)
        else:
            try:
                if chat.unread_count and chat.unread_count > 0:
                    client.chats.mark_read(chat_id=chat.id)
            except Exception:
                pass
            client.chats.archive(chat_id=chat.id, archived=True)
            archived.append(item)

    print(
        json.dumps(
            {
                "mode": "dry_run" if args.dry_run else "archived",
                "count": len(candidates) if args.dry_run else len(archived),
                "items": candidates if args.dry_run else archived,
            },
            indent=2,
        )
    )


def cmd_remind(args: argparse.Namespace) -> None:
    client = get_client()
    remind_dt = datetime.fromisoformat(args.remind_at)
    chat = client.chats.retrieve(args.chat_id)
    client.chats.reminders.create(
        chat_id=args.chat_id,
        reminder={
            "remind_at": remind_dt,
            "dismiss_on_incoming_message": not args.keep_on_reply,
        },
    )

    gcal_link = None
    if not args.no_phone_calendar:
        end_dt = remind_dt + timedelta(minutes=15)
        note_suffix = f" - {args.note}" if getattr(args, "note", "") else ""
        summary = f"[Follow Up] {chat.title}{note_suffix}"
        gcal_bin = str(Path.home() / ".gemini" / "antigravity" / "bin" / "gcalendar")
        if not Path(gcal_bin).exists():
            gcal_bin = "gcalendar"
        cmd = [
            gcal_bin,
            "mutate",
            "create",
            "--cal",
            os.environ.get("CLAIRE_PRIMARY_CALENDAR_ID", "primary"),
            "--summary",
            summary,
            "--description",
            f"Beeper SMS/RCS follow-up reminder for {chat.title} (chat_id: {args.chat_id}).",
            "--start",
            remind_dt.isoformat(),
            "--end",
            end_dt.isoformat(),
            "--transparency",
            "transparent",
            "--send-updates",
            "none",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        for line in proc.stdout.splitlines():
            if line.startswith("Link:"):
                gcal_link = line.split("Link:", 1)[1].strip()

    print(
        json.dumps(
            {
                "status": "reminder_set",
                "chat_id": args.chat_id,
                "chat_title": chat.title,
                "remind_at": remind_dt.isoformat(),
                "dismiss_on_incoming_message": not args.keep_on_reply,
                "calendar_link": gcal_link,
            },
            indent=2,
        )
    )


def mutate_manifest_locked(mutator: Callable[[dict[str, Any]], Any]) -> Any:
    """Atomically read-modify-write state/epistemic_manifest.json under fcntl.flock."""
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH, "a+", encoding="utf-8") as mf:
        fcntl.flock(mf.fileno(), fcntl.LOCK_EX)
        mf.seek(0)
        raw = mf.read().strip()
        try:
            data = json.loads(raw) if raw else {}
            if not isinstance(data, dict):
                data = {}
        except json.JSONDecodeError:
            data = {}
        result = mutator(data)
        mf.seek(0)
        mf.truncate()
        mf.write(json.dumps(data, indent=2) + "\n")
        mf.flush()
        os.fsync(mf.fileno())
        return result


def _sample_thread_messages(
    client: Any, chat_id: str, count: int = 50, context_count: int = 10
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    msgs_iter = client.messages.list(chat_id)
    recent_context: list[dict[str, Any]] = []
    my_messages: list[str] = []
    inspected = 0
    latest_message_id: str | None = None

    for m in msgs_iter:
        inspected += 1
        if latest_message_id is None:
            latest_message_id = str(getattr(m, "id", "") or "") or None
        text = (getattr(m, "text", None) or "").strip()
        is_sender = bool(getattr(m, "is_sender", False))
        ts = m.timestamp.isoformat() if hasattr(m.timestamp, "isoformat") else str(m.timestamp)

        if len(recent_context) < context_count and text:
            recent_context.append(
                {
                    "id": m.id,
                    "timestamp": ts,
                    "sender_name": "Operator (me)" if is_sender else (getattr(m, "sender_name", None) or m.sender_id),
                    "is_sender": is_sender,
                    "text": text,
                }
            )
        if is_sender and text and len(my_messages) < count:
            my_messages.append(text)
        if len(my_messages) >= count and len(recent_context) >= context_count:
            break
        if inspected >= 600:
            break

    return recent_context, my_messages, latest_message_id


def cmd_voice_sample(args: argparse.Namespace) -> None:
    client = get_client()
    chat = client.chats.retrieve(args.chat_id)
    recent_context, my_messages, latest_message_id = _sample_thread_messages(
        client, args.chat_id, count=args.count, context_count=args.context_count
    )
    now_iso = datetime.now(timezone.utc).isoformat()

    def _record_sample(data: dict[str, Any]) -> None:
        data.setdefault("voice_samples", {})[chat.id] = {
            "sampled_at": now_iso,
            "latest_message_id": latest_message_id,
            "my_message_count": len(my_messages),
            "sample_preview": my_messages[:5],
        }

    mutate_manifest_locked(_record_sample)
    print(
        json.dumps(
            {
                "chat_id": chat.id,
                "chat_title": chat.title,
                "sampled_at": now_iso,
                "latest_message_id": latest_message_id,
                "my_message_count": len(my_messages),
                "recent_thread_context": recent_context,
                "my_last_messages": my_messages,
            },
            indent=2,
        )
    )


def _fetch_latest_chat_message(client: Any, chat_id: str) -> tuple[str | None, str | None, bool]:
    try:
        items = list(client.messages.list(chat_id).items)
        if items:
            m0 = items[0]
            return (
                str(getattr(m0, "id", "") or "") or None,
                str(getattr(m0, "text", "") or "")[:120],
                bool(getattr(m0, "is_sender", False)),
            )
    except Exception:
        pass
    return None, None, False


def cmd_stage_reply(args: argparse.Namespace) -> None:
    sec_err = lint_security_payload("stage-reply", args.text)
    if sec_err:
        sys.stderr.write(f"Error ({sec_err})\n")
        sys.exit(1)
    slop_err = lint_markdown_slop("stage-reply.md", args.text)
    if slop_err:
        sys.stderr.write(f"Error ({slop_err})\n")
        sys.exit(1)

    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    client = get_client()
    chat = client.chats.retrieve(args.chat_id)

    # Check if a fresh (<24h) voice sample exists; if not, auto-sample on the fly so stage-reply never deadlocks.
    voice_rec = mutate_manifest_locked(lambda d: (d.get("voice_samples") or {}).get(chat.id))
    voice_sampled_at: str | None = None
    voice_preview: list[str] = []
    voice_fresh = False
    if isinstance(voice_rec, dict) and voice_rec.get("sampled_at"):
        voice_sampled_at = str(voice_rec["sampled_at"])
        voice_preview = list(voice_rec.get("sample_preview") or [])
        try:
            v_dt = datetime.fromisoformat(voice_sampled_at)
            if v_dt.tzinfo is None:
                v_dt = v_dt.replace(tzinfo=timezone.utc)
            voice_fresh = (now_dt - v_dt).total_seconds() <= VOICE_SAMPLE_TTL_SECONDS
        except ValueError:
            voice_fresh = False

    if not voice_fresh:
        _, my_msgs, sampled_latest_id = _sample_thread_messages(client, chat.id, count=25, context_count=5)
        voice_sampled_at = now_iso
        voice_preview = my_msgs[:5]
        latest_msg_id = sampled_latest_id
    else:
        latest_msg_id, _, _ = _fetch_latest_chat_message(client, chat.id)

    digest = hashlib.sha256(f"{args.chat_id}:{args.text}:{now_iso}".encode("utf-8")).hexdigest()[:6]
    approval_id = f"RPL-{digest}"

    def _stage_in_manifest(data: dict[str, Any]) -> dict[str, Any]:
        if not voice_fresh:
            data.setdefault("voice_samples", {})[chat.id] = {
                "sampled_at": voice_sampled_at,
                "latest_message_id": latest_msg_id,
                "my_message_count": len(voice_preview),
                "sample_preview": voice_preview,
            }
        rpl_map = data.setdefault("staged_replies", {})
        for old_rec in rpl_map.values():
            if not isinstance(old_rec, dict) or old_rec.get("status") != "STAGED":
                continue
            if old_rec.get("chat_id") == chat.id:
                old_rec["status"] = "SUPERSEDED"
                old_rec["superseded_by"] = approval_id
                continue
            old_ts = str(old_rec.get("ts") or old_rec.get("staged_at") or "")
            if old_ts:
                try:
                    o_dt = datetime.fromisoformat(old_ts)
                    if o_dt.tzinfo is None:
                        o_dt = o_dt.replace(tzinfo=timezone.utc)
                    if (now_dt - o_dt).total_seconds() > VOICE_SAMPLE_TTL_SECONDS:
                        old_rec["status"] = "EXPIRED"
                except ValueError:
                    pass
        if len(rpl_map) > 64:
            keep_r = list(rpl_map.keys())[-32:]
            data["staged_replies"] = {k: rpl_map[k] for k in keep_r}
            rpl_map = data["staged_replies"]
        rec = {
            "approval_id": approval_id,
            "chat_id": chat.id,
            "chat_title": chat.title,
            "network": chat.network,
            "text": args.text,
            "reply_to_message_id": args.reply_to,
            "latest_message_id": latest_msg_id or (voice_rec or {}).get("latest_message_id"),
            "voice_sampled_at": voice_sampled_at,
            "voice_sample_preview": voice_preview,
            "ts": now_iso,
            "staged_at": now_iso,
            "user_input_sha256": data.get("last_user_input_sha256") or "cli",
            "status": "STAGED",
        }
        rpl_map[approval_id] = rec
        data["updated_at"] = now_iso
        return rec

    entry = mutate_manifest_locked(_stage_in_manifest)
    print(json.dumps(entry, indent=2))


def cmd_send_staged(args: argparse.Namespace) -> None:
    entry = mutate_manifest_locked(lambda d: (d.get("staged_replies") or {}).get(args.approval_id))
    if not isinstance(entry, dict):
        sys.stderr.write(f"Error: approval_id '{args.approval_id}' not found in {MANIFEST_PATH}.\n")
        sys.exit(1)
    if entry.get("status") != "STAGED":
        sys.stderr.write(f"Error: approval_id '{args.approval_id}' has status '{entry.get('status')}'.\n")
        sys.exit(1)

    client = get_client()
    expected_latest_id = entry.get("latest_message_id")
    if expected_latest_id and not getattr(args, "force_stale", False):
        curr_latest_id, curr_preview, curr_is_sender = _fetch_latest_chat_message(client, entry["chat_id"])
        if curr_latest_id and curr_latest_id != expected_latest_id and not curr_is_sender:
            sys.stderr.write(
                f"Error (Stale-Draft Race Gate): Recipient sent a newer message ({curr_latest_id}: "
                f"{curr_preview!r}) after {args.approval_id} was staged ({expected_latest_id}). "
                "Re-read the thread and stage an updated reply, or pass --force-stale.\n"
            )
            sys.exit(1)

    send_kwargs: dict[str, Any] = {"text": entry["text"]}
    if entry.get("reply_to_message_id"):
        send_kwargs["reply_to_message_id"] = entry["reply_to_message_id"]

    resp = client.messages.send(entry["chat_id"], **send_kwargs)
    try:
        client.chats.mark_read(chat_id=entry["chat_id"])
    except Exception:
        pass
    sent_iso = datetime.now(timezone.utc).isoformat()
    pending_msg_id = getattr(resp, "pending_message_id", None)

    def _mark_sent(data: dict[str, Any]) -> dict[str, Any]:
        rec = data.setdefault("staged_replies", {}).get(args.approval_id) or entry
        rec["status"] = "SENT"
        rec["sent_ts"] = sent_iso
        rec["sent_at"] = sent_iso
        rec["pending_message_id"] = pending_msg_id
        data["staged_replies"][args.approval_id] = rec
        data["updated_at"] = sent_iso
        return rec

    updated_entry = mutate_manifest_locked(_mark_sent)
    print(json.dumps(updated_entry, indent=2))


def cmd_oauth_login(args: argparse.Namespace) -> None:
    callback_port = args.port
    redirect_uri = f"http://127.0.0.1:{callback_port}/callback"

    reg_payload = json.dumps(
        {
            "client_name": "Antigravity (Claire SMS Triage)",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "scope": "read write",
            "token_endpoint_auth_method": "none",
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{DEFAULT_BASE_URL}/oauth/register",
        data=reg_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        reg_data = json.loads(resp.read().decode("utf-8"))

    client_id = reg_data["client_id"]
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    state = secrets.token_hex(16)

    auth_params = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "read write",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
    )
    auth_url = f"{DEFAULT_BASE_URL}/oauth/authorize?{auth_params}"

    captured: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            if "code" in qs:
                captured["code"] = qs["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><body style='font-family:sans-serif;padding:2rem;'>"
                    b"<h2>Antigravity (Claire) Connected to Beeper Desktop</h2>"
                    b"<p>You can close this tab and return to Antigravity.</p></body></html>"
                )
            else:
                captured["error"] = qs.get("error", ["unknown"])[0]
                self.send_response(400)
                self.end_headers()

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", callback_port), CallbackHandler)
    server.timeout = args.timeout
    print(f"Opening Beeper consent screen in browser: {auth_url}", flush=True)
    subprocess.run(["open", auth_url], check=False)
    server.handle_request()
    server.server_close()

    if "code" not in captured:
        sys.stderr.write(f"Error: OAuth approval did not complete ({captured.get('error', 'timeout')}).\n")
        sys.exit(1)

    token_body = urlencode(
        {
            "grant_type": "authorization_code",
            "code": captured["code"],
            "code_verifier": verifier,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    token_req = urllib.request.Request(
        f"{DEFAULT_BASE_URL}/oauth/token",
        data=token_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(token_req, timeout=10) as resp:
        token_data = json.loads(resp.read().decode("utf-8"))

    access_token = token_data["access_token"]
    args.token = access_token
    cmd_configure_token(args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Beeper Desktop SMS/RCS Triage & Gated Reply CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_oauth = sub.add_parser("oauth-login", help="Run interactive PKCE OAuth flow with Beeper Desktop")
    p_oauth.add_argument("--port", type=int, default=23379, help="Local callback port")
    p_oauth.add_argument("--timeout", type=int, default=120, help="Seconds to wait for user click")
    p_oauth.set_defaults(func=cmd_oauth_login)

    p_cfg = sub.add_parser("configure-token", help="Save Beeper Desktop API token and wire MCP config")
    p_cfg.add_argument("--token", required=True, help="Beeper Desktop API access token")
    p_cfg.set_defaults(func=cmd_configure_token)

    p_tri = sub.add_parser("triage", help="Fetch recent active or unread SMS/RCS threads and messages")
    p_tri.add_argument("--hours", type=int, default=24, help="Lookback window in hours (default: 24)")
    p_tri.add_argument("--limit", type=int, default=25, help="Max chats to inspect (default: 25)")
    p_tri.add_argument("--messages-per-chat", type=int, default=8, help="Recent messages per chat (default: 8)")
    p_tri.add_argument("--unread-only", action="store_true", help="Only fetch threads with unread messages")
    p_tri.add_argument("--sms-only", action="store_true", help="Filter strictly to Google Messages / SMS / RCS")
    p_tri.add_argument(
        "--include-shortcodes",
        action="store_true",
        help="Include automated 5/6-digit shortcode threads (excluded by default)",
    )
    p_tri.add_argument("--account-id", default="", help="Optional specific Beeper account_id filter")
    p_tri.set_defaults(func=cmd_triage)

    p_srch = sub.add_parser("search", help="Search message history across threads")
    p_srch.add_argument("--query", required=True, help="Literal search query")
    p_srch.add_argument("--limit", type=int, default=20, help="Max messages to return")
    p_srch.set_defaults(func=cmd_search)

    p_cln = sub.add_parser(
        "clean-inbox",
        help="Archive 5/6-digit shortcodes, bank alerts, OTPs, and marketing spam out of Primary inbox",
    )
    p_cln.add_argument("--limit", type=int, default=200, help="Max primary chats to scan (default: 200)")
    p_cln.add_argument("--dry-run", action="store_true", help="List noise threads without archiving yet")
    p_cln.add_argument("--inspect-messages", action="store_true", help="Include 1-line preview of latest text")
    p_cln.set_defaults(func=cmd_clean_inbox)

    p_rem = sub.add_parser("remind", help="Set a native Beeper follow-up reminder + mobile calendar ping")
    p_rem.add_argument("--chat-id", required=True, help="Target chat_id")
    p_rem.add_argument("--remind-at", required=True, help="ISO 8601 timestamp for reminder")
    p_rem.add_argument("--note", default="", help="Short follow-up note for the mobile calendar event")
    p_rem.add_argument("--no-phone-calendar", action="store_true", help="Skip creating the mobile calendar ping")
    p_rem.add_argument("--keep-on-reply", action="store_true", help="Do not auto-dismiss if contact replies first")
    p_rem.set_defaults(func=cmd_remind)

    p_vce = sub.add_parser(
        "voice-sample",
        help="Pull operator's last N outgoing messages in a thread to match exact per-chat texting register",
    )
    p_vce.add_argument("--chat-id", required=True, help="Target chat_id")
    p_vce.add_argument("--count", type=int, default=50, help="Number of outgoing messages to sample (default: 50)")
    p_vce.add_argument("--context-count", type=int, default=10, help="Number of recent thread messages for context (default: 10)")
    p_vce.set_defaults(func=cmd_voice_sample)

    p_stg = sub.add_parser("stage-reply", help="Stage a prefabricated reply for operator approval")
    p_stg.add_argument("--chat-id", required=True, help="Target chat_id")
    p_stg.add_argument("--text", required=True, help="Draft message text")
    p_stg.add_argument("--reply-to", default=None, help="Optional message_id to reply to")
    p_stg.set_defaults(func=cmd_stage_reply)

    p_snd = sub.add_parser("send-staged", help="Send a previously staged reply after explicit operator approval")
    p_snd.add_argument("--approval-id", required=True, help="Approval ID (e.g. RPL-xxxxxx)")
    p_snd.add_argument(
        "--force-stale",
        action="store_true",
        help="Send even if a newer incoming message arrived in the thread after staging",
    )
    p_snd.set_defaults(func=cmd_send_staged)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
