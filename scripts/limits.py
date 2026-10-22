#!/usr/bin/env -S python3 -I
"""Plan limits (5-hour session, weekly, monthly) for your agent accounts.

  limits.py status        one line for the selected account
  limits.py short         compact quota of the selected account (header)
  limits.py list          account picker rows for fzf
  limits.py show <id>     detailed view with bars (fzf preview)
  limits.py select <id>   choose which account the header shows
  limits.py login <id>    sign that account in (its own folder, email pre-filled)
  limits.py open <id>     open that account in a new herdr pane, continuing the last chat
  limits.py link <id>     link ~/.claude's shared files into a Claude account's folder
  limits.py json          every account and its fuel as JSON (orcha's FUEL and accounts popup)
  limits.py switch <id> <pane>   continue that pane's conversation on another account
  limits.py login-pane <id>      sign that account in, in a new pane

Accounts come from `accounts` in ~/.config/orcha/config.json.
Claude: Anthropic's usage endpoint, with the account's own sign-in from the
macOS Keychain (the same data as /usage). Codex: the rate limits Codex writes
into its newest session log. Cursor: the usage summary of cursor.com.
Tokens are never printed or refreshed here, and reach curl on stdin only.
"""
import base64
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "lib"))
import orchalib as o  # noqa: E402

CFG = o.load_config()
T = o.strings(CFG["lang"])
P = o.palette(CFG)
ACCOUNTS = {a["id"]: a for a in CFG["accounts"]}
CACHE_DIR = os.path.join(o.CACHE, "limits")
SELECTED_FILE = os.path.join(o.CONFIG_DIR, "selected-account")
CACHE_TTL = 120
# the agent logos of the Agent Icons font, else a symbol
LOGO = ({"claude": "\U00100000", "codex": "\U00100001", "cursor": "\U00100002"} if CFG["widgets"]["logos"]
        else {k: v[2] for k, v in o.AGENTS.items()})

# (key, header title, detail title, picker tag)
WINDOWS = (("session", T["lim_session"], T["lim_session_long"], "5h"),
           ("weekly", T["lim_week"], T["lim_week"], T["lim_week_tag"]),
           ("monthly", T["lim_month"], T["lim_month_long"], T["lim_month_tag"]))

ACCENT, MUTED, TEXT = P["accent"], P["muted"], P["fg"]
GREEN, YELLOW, RED = P["green"], P["yellow"], P["red"]
fg = o.fg


DEMO = os.path.exists(os.path.join(o.CONFIG_DIR, "demo"))  # screenshots: no real emails


def email(acct):
    """Signed-in email of an account, or None (only the email is read)."""
    cfg = ACCOUNTS[acct]
    if DEMO:
        return "you@example.com" if cfg["kind"] != "claude" or not cfg.get("dir") else "me@example.com"
    try:
        if cfg["kind"] == "claude":
            d = o.claude_dir(cfg)
            path = os.path.join(d, ".claude.json") if d else os.path.join(o.HOME, ".claude.json")
            with open(path) as fh:
                return (json.load(fh).get("oauthAccount") or {}).get("emailAddress")
        if cfg["kind"] == "cursor":
            with open(os.path.join(o.HOME, ".cursor", "cli-config.json")) as fh:
                return (json.load(fh).get("authInfo") or {}).get("email")
        with open(os.path.join(o.HOME, ".codex", "auth.json")) as fh:
            id_token = (json.load(fh).get("tokens") or {}).get("id_token", "")
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email")
    except Exception:
        return None


def account_title(acct):
    e = email(acct)
    label = ACCOUNTS[acct].get("label") or acct
    if not e:
        return f"{label} ({T['lim_signed_out']})"
    expect = ACCOUNTS[acct].get("email")
    wrong = expect and e.lower() != expect.lower()
    return f"{label} ({e}{' ⚠ ' + T['lim_wrong_account'] if wrong else ''})"


def level(pct):
    return GREEN if pct < 50 else YELLOW if pct < 80 else RED


def until(ts):
    secs = int(ts - time.time())
    if secs <= 0:
        return T["lim_now"]
    d, rem = divmod(secs, 86400)
    h, m = divmod(rem // 60, 60)
    return f"{d}d {h}h" if d else f"{h}h {m:02d}m" if h else f"{m}m"


def iso_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


# ---------- fetchers: {"session": (pct, reset_ts), "weekly": (...), "note": str} or {"error": str}

def claude_limits(cfg):
    service = "Claude Code-credentials"
    d = o.claude_dir(cfg)
    if d:
        service += "-" + hashlib.sha256(d.encode()).hexdigest()[:8]
    try:
        raw = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                             capture_output=True, text=True, timeout=10).stdout
        oauth = json.loads(raw)["claudeAiOauth"]
    except Exception:
        return {"error": T["lim_err_signin"]}
    if oauth.get("expiresAt", 0) / 1000 < time.time():
        return {"error": T["lim_err_expired"]}
    # curl (system certs) with headers fed on stdin, so the token never shows in `ps`.
    curl_cfg = (f'header = "Authorization: Bearer {oauth["accessToken"]}"\n'
                'header = "anthropic-beta: oauth-2025-04-20"\n'
                'header = "Content-Type: application/json"\n')
    try:
        r = subprocess.run(["curl", "-sS", "-m", "15", "-f", "-K", "-",
                            "https://api.anthropic.com/api/oauth/usage"],
                           input=curl_cfg, capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return {"error": f"{T['lim_err_request']} ({r.stderr.strip()[:80]})"}
        data = json.loads(r.stdout)
    except Exception as e:
        return {"error": f"{T['lim_err_request']} ({e})"}
    out = {}
    for key, name in (("five_hour", "session"), ("seven_day", "weekly")):
        w = data.get(key) or {}
        if w.get("utilization") is not None:
            out[name] = (float(w["utilization"]), iso_ts(w["resets_at"]) if w.get("resets_at") else None)
    return out or {"error": T["lim_err_nodata"]}


def codex_limits(_cfg):
    files = sorted(glob.glob(os.path.join(o.HOME, ".codex", "sessions", "*", "*", "*", "*.jsonl")),
                   key=os.path.getmtime, reverse=True)[:10]
    for f in files:
        rl = None
        with open(f, errors="ignore") as fh:
            for line in fh:
                if '"rate_limits"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                cand = (obj.get("payload") or {}).get("rate_limits") or obj.get("rate_limits")
                if cand and cand.get("primary"):
                    rl = cand
        if not rl:
            continue
        out = {"note": T["lim_codex_note"] + " " + datetime.fromtimestamp(os.path.getmtime(f)).strftime("%a %H:%M")}
        for key, name in (("primary", "session"), ("secondary", "weekly")):
            w = rl.get(key) or {}
            reset = w.get("resets_at")
            pct = float(w.get("used_percent", 0))
            if reset and reset < time.time():
                pct = 0.0  # the window already reset since that run
            out[name] = (pct, reset)
        return out
    return {"error": T["lim_err_codex"]}


def cursor_limits(_cfg):
    try:
        tok = subprocess.run(["security", "find-generic-password", "-s", "cursor-access-token", "-w"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        sub = json.loads(base64.urlsafe_b64decode(payload))["sub"].split("|")[-1]
    except Exception:
        return {"error": T["lim_err_cursor"]}
    # Same session cookie the Cursor dashboard uses; fed on stdin, never in `ps`.
    curl_cfg = f'header = "Cookie: WorkosCursorSessionToken={sub}%3A%3A{tok}"\n'
    try:
        r = subprocess.run(["curl", "-sS", "-m", "15", "-f", "-K", "-", "https://cursor.com/api/usage-summary"],
                           input=curl_cfg, capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return {"error": T["lim_err_cursor_expired"]}
        data = json.loads(r.stdout)
        plan = data["individualUsage"]["plan"]
    except Exception as e:
        return {"error": f"{T['lim_err_request']} ({e})"}
    return {"monthly": (float(plan.get("totalPercentUsed", 0)), iso_ts(data["billingCycleEnd"])),
            "note": T["lim_cursor_note"].format(plan=data.get("membershipType", "?"),
                                                auto=f"{plan.get('autoPercentUsed', 0):.0f}",
                                                api=f"{plan.get('apiPercentUsed', 0):.0f}")}


def get(acct, fresh=False):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, acct + ".json")
    if not fresh and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL:
        with open(path) as fh:
            return json.load(fh)
    cfg = ACCOUNTS[acct]
    data = {"claude": claude_limits, "codex": codex_limits, "cursor": cursor_limits}[cfg["kind"]](cfg)
    with open(path, "w") as fh:
        json.dump(data, fh)
    return data


def selected():
    try:
        with open(SELECTED_FILE) as fh:
            a = fh.read().strip()
        if a in ACCOUNTS:
            return a
    except OSError:
        pass
    return next(iter(ACCOUNTS), None)


def pane_account():
    """The account the agent in the focused pane runs on, or None.
    Claude: its CLAUDE_CONFIG_DIR, read from the process (`ps -E`); none means ~/.claude."""
    pane = os.environ.get("HERDR_ACTIVE_PANE_ID")
    if not pane:
        return None
    try:
        got = json.loads(subprocess.run(["herdr", "pane", "get", pane], capture_output=True, text=True,
                                        timeout=5).stdout)["result"]["pane"]
        kind = got.get("agent")
        if kind in ("codex", "cursor"):
            return next((a for a, c in ACCOUNTS.items() if c["kind"] == kind), None)
        if kind != "claude":
            return None
        info = json.loads(subprocess.run(["herdr", "pane", "process-info", "--pane", pane], capture_output=True,
                                         text=True, timeout=5).stdout)["result"]["process_info"]
        found = None
        for p in info.get("foreground_processes", []):
            env = subprocess.run(["ps", "-E", "-ww", "-o", "command=", "-p", str(p["pid"])],
                                 capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"(?:^|\s)CLAUDE_CONFIG_DIR=(\S+)", env)
            if m:
                found = os.path.realpath(os.path.expanduser(m.group(1)))
                break
        for a, cfg in ACCOUNTS.items():
            if cfg["kind"] != "claude":
                continue
            d = o.claude_dir(cfg)
            if (found and d and os.path.realpath(d) == found) or (not found and not d):
                return a
    except Exception:
        pass
    return None


# ---------- commands

def text_bar(pct, width=10):
    filled = round(min(pct, 100) / 100 * width)
    return "█" * filled + "░" * (width - filled)


def cmd_status():
    # Plain text only: herdr strips colors from tab-bar commands.
    acct = selected()
    if not acct:
        return
    d = get(acct)
    if "error" in d:
        print(f"󰚩 {account_title(acct)}  ·  {T['lim_no_data']}")
        return
    parts = []
    for key, title, _, _ in WINDOWS:
        if key in d:
            pct, reset = d[key]
            part = f"{title} {text_bar(pct)} {pct:.0f}%"
            if reset:
                part += f" ({T['lim_resets']} {until(reset)})"
            parts.append(part)
    print(f"󰚩 {account_title(acct)}  ·  " + "  ·  ".join(parts))


def cmd_short():
    # "󰚩 5h ███░░ 30%": the window that runs out first (5h, else the month for Cursor),
    # of the focused agent's account, else the account picked in the picker
    acct = pane_account() or selected()
    if not acct:
        return
    d = get(acct)
    if "error" in d:
        return
    # whose quota: the agent's logo and the account's email
    kind = ACCOUNTS[acct]["kind"]
    logo = LOGO.get(kind, "󰚩")
    who = email(acct) or ACCOUNTS[acct].get("label") or acct
    for key, _, _, tag in WINDOWS:
        if key in d:
            pct = d[key][0]
            print(f"{logo} {who} · {tag} {text_bar(pct, 5)} {pct:.0f}%")
            return


def cmd_list():
    sel = selected()
    for acct in ACCOUNTS:
        d = get(acct)
        if "error" in d:
            summary = "—"
        else:
            summary = "  ".join(f"{t} {d[n][0]:>3.0f}%" for n, _, _, t in WINDOWS if n in d)
        mark = "●" if acct == sel else " "
        print(f"{acct}\t{mark} {account_title(acct):<46}  {summary}")


def bar(pct, width=28):
    filled = round(min(pct, 100) / 100 * width)
    return fg(level(pct), "█" * filled) + fg(P["surface1"], "░" * (width - filled))


def cmd_show(acct):
    d = get(acct, fresh=True)
    shown = "   " + fg(MUTED, f"({T['lim_shown']})") if acct == selected() else ""
    print(fg(ACCENT, f"  {account_title(acct)}", bold=True) + shown)
    print()
    if "error" in d:
        print("  " + fg(YELLOW, d["error"]))
        return
    for name, _, title, _ in WINDOWS:
        if name not in d:
            continue
        pct, reset = d[name]
        left = max(0.0, 100 - pct)
        print("  " + fg(TEXT, title, bold=True))
        used = fg(level(pct), f"{pct:.0f}% {T['lim_used']}", bold=True)
        print(f"  {bar(pct)}  {used}  {fg(MUTED, f'{left:.0f}% ' + T['lim_left'])}")
        if reset:
            when = datetime.fromtimestamp(reset).strftime("%a %d %b %H:%M")
            print("  " + fg(MUTED, f"{T['lim_resets_in']} {until(reset)}  ({when})"))
        print()
    if d.get("note"):
        print("  " + fg(MUTED, d["note"]))


def link_shared(target):
    """An extra Claude account's folder: everything of ~/.claude linked in
    (history, settings, skills...) except the sign-in files, so all accounts
    share their conversations."""
    src = os.path.join(o.HOME, ".claude")
    private = (".claude.json", ".credentials.json", "daemon", "daemon.log", "daemon-auth-cooldown",
               "daemon-auth-status.json", "backups")
    os.makedirs(target, exist_ok=True)
    for name in os.listdir(src) if os.path.isdir(src) else []:
        if (name in private or name.startswith(("policy-limits.json", "remote-settings.json", ".."))
                or ".tmp" in name):
            continue
        dst = os.path.join(target, name)
        if not os.path.lexists(dst):
            os.symlink(os.path.join(src, name), dst)


def env_for(acct):
    env = dict(os.environ)
    env.pop("CLAUDE_CONFIG_DIR", None)
    d = o.claude_dir(ACCOUNTS[acct]) if ACCOUNTS[acct]["kind"] == "claude" else None
    if d:
        env["CLAUDE_CONFIG_DIR"] = d
    return env


def cmd_login(acct):
    """Sign this account in, in its own folder, with its email filled in."""
    cfg = ACCOUNTS[acct]
    who = f"  →  {cfg['email']}" if cfg.get("email") else ""
    print(fg(ACCENT, f"  {T['lim_login']} {cfg.get('label') or acct}{who}", bold=True))
    print(fg(MUTED, f"  {T['lim_login_hint']}\n"))
    if cfg["kind"] == "claude":
        d = o.claude_dir(cfg)
        if d:
            link_shared(d)
        cmd = ["claude", "auth", "login"] + (["--email", cfg["email"]] if cfg.get("email") else [])
    elif cfg["kind"] == "codex":
        cmd = ["codex", "login"]
    else:
        cmd = ["cursor-agent", "login"]
    subprocess.run(cmd, env=env_for(acct))
    try:
        os.remove(os.path.join(CACHE_DIR, acct + ".json"))
    except OSError:
        pass
    print("\n  " + account_title(acct))
    input(fg(MUTED, f"\n  {T['lim_enter_back']}"))


def cmd_open(acct):
    """Open this account in a new pane next to the current one, continuing the last chat."""
    cfg = ACCOUNTS[acct]
    cwd = os.environ.get("HERDR_ACTIVE_PANE_CWD") or o.HOME
    split = ["herdr", "pane", "split", "--current", "--direction", "right", "--cwd", cwd]
    d = o.claude_dir(cfg) if cfg["kind"] == "claude" else None
    if d:
        link_shared(d)
        split += ["--env", f"CLAUDE_CONFIG_DIR={d}"]
    out = subprocess.run(split, capture_output=True, text=True).stdout
    try:
        pane = json.loads(out)["result"]["pane"]["pane_id"]
    except Exception:
        print(T["lim_open_failed"] + ": " + out[:200])
        input("Enter...")
        return
    run = {"claude": "claude --continue || claude",
           "codex": "codex resume --last || codex",
           "cursor": "cursor-agent resume || cursor-agent"}[cfg["kind"]]
    subprocess.run(["herdr", "pane", "run", pane, run], capture_output=True)


WINDOW_TAGS = {"session": "5H", "weekly": "WEEK", "monthly": "MONTH"}


def cmd_json():
    """[{id, kind, label, email, current, error, windows: [{key, label, left, reset}]}]
    `left` is the % left of the window; `current` is the focused agent's account
    (HERDR_ACTIVE_PANE_ID), else the one picked in the picker."""
    cur = pane_account() or selected()
    out = []
    for acct, cfg in ACCOUNTS.items():
        d = get(acct)
        windows = []
        for key, _, _, _ in WINDOWS:
            if key in d:
                pct, reset = d[key]
                windows.append({"key": key, "label": WINDOW_TAGS[key],
                                "left": max(0, min(100, round(100 - pct))),
                                "reset": until(reset) if reset else None})
        out.append({"id": acct, "kind": cfg["kind"], "label": cfg.get("label") or acct,
                    "email": email(acct), "current": acct == cur,
                    "error": d.get("error"), "windows": windows})
    print(json.dumps(out))


def cmd_switch(acct, pane):
    """Stop the agent in `pane` and continue the same conversation there on
    another account (`--resume <session id>`, shared history)."""
    cfg = ACCOUNTS[acct]
    try:
        got = json.loads(subprocess.run(["herdr", "pane", "get", pane], capture_output=True, text=True,
                                        timeout=5).stdout)["result"]["pane"]
    except Exception:
        sys.exit(T["lim_open_failed"])
    session = (got.get("agent_session") or {}).get("value")
    kind = cfg["kind"]
    d = o.claude_dir(cfg) if kind == "claude" else None
    if d:
        link_shared(d)
    if kind == "claude":
        env = f"env CLAUDE_CONFIG_DIR={d}" if d else "env -u CLAUDE_CONFIG_DIR"
        run = f"{env} claude --resume {session}" if session else f"{env} claude --continue"
    elif kind == "codex":
        run = f"codex resume {session}" if session else "codex resume --last"
    else:
        run = "cursor-agent resume"
    # Ctrl+C twice leaves Claude Code and Codex; then the shell runs the resume
    for _ in range(2):
        subprocess.run(["herdr", "pane", "send-keys", pane, "ctrl+c"], capture_output=True)
        time.sleep(0.4)
    time.sleep(1.0)
    subprocess.run(["herdr", "pane", "run", pane, run], capture_output=True)
    cmd_select(acct)


def cmd_login_pane(acct):
    """`login` in a new pane next to the focused one (orcha's SIGN IN)."""
    pane = os.environ.get("HERDR_ACTIVE_PANE_ID")
    split = ["herdr", "pane", "split", "--direction", "right"]
    split += ["--pane", pane] if pane else ["--current"]
    out = subprocess.run(split, capture_output=True, text=True).stdout
    try:
        new = json.loads(out)["result"]["pane"]["pane_id"]
    except Exception:
        sys.exit(T["lim_open_failed"])
    me = os.path.realpath(__file__)
    subprocess.run(["herdr", "pane", "run", new, f"{me} login {acct}"], capture_output=True)


def cmd_select(acct):
    os.makedirs(o.CONFIG_DIR, exist_ok=True)
    with open(SELECTED_FILE, "w") as fh:
        fh.write(acct + "\n")


def main():
    args = sys.argv[1:] or ["status"]
    cmd = args[0]
    if cmd == "status":
        cmd_status()
    elif cmd == "short":
        cmd_short()
    elif cmd == "list":
        cmd_list()
    elif cmd == "json":
        cmd_json()
    elif cmd == "switch" and len(args) > 2 and args[1] in ACCOUNTS:
        cmd_switch(args[1], args[2])
    elif cmd == "link" and len(args) > 1 and args[1] in ACCOUNTS:
        d = o.claude_dir(ACCOUNTS[args[1]])
        if d:
            link_shared(d)
    elif cmd in ("show", "select", "login", "login-pane", "open") and len(args) > 1 and args[1] in ACCOUNTS:
        {"show": cmd_show, "select": cmd_select, "login": cmd_login, "login-pane": cmd_login_pane,
         "open": cmd_open}[cmd](args[1])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
