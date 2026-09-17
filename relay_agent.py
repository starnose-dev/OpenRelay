#!/usr/bin/env python3
"""OpenRelay — clean-room rebuild of Relay.app's core capability.

Relay.app (AI workflow automation, "the new Zapier") shut down on
September 14, 2026. This rebuilds the lost core: AI agents that watch an
inbox and turn email into triage, summaries, reply drafts, tasks, and
structured data — running locally, free, forever.

Usage:
    python3 relay_agent.py run --inbox inbox_sample.json --workflows workflows.json
    python3 relay_agent.py run --inbox ... --workflows ... --once   # same; default is one pass
    python3 relay_agent.py init                                     # create out/ dirs

Inbox sources: a JSON file (demo / tests) or IMAP (--imap-* flags).
Workflows: JSON file; see workflows.json for the schema.
"""
import argparse
import csv
import imaplib
import email as email_lib
import json
import os
import re
import sys
from datetime import datetime, timezone

from brain import Brain

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "out")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- inbox
def load_json_inbox(path):
    with open(path) as f:
        items = json.load(f)
    out = []
    for i, m in enumerate(items):
        out.append({
            "id": m.get("id", "msg-%d" % i),
            "from": m.get("from", ""),
            "subject": m.get("subject", ""),
            "body": m.get("body", ""),
            "date": m.get("date", ""),
        })
    return out


def load_imap_inbox(host, user, password, folder="INBOX", limit=25):
    """Real IMAP source (stdlib). Credentials via flags/env, never stored."""
    conn = imaplib.IMAP4_SSL(host)
    conn.login(user, password)
    conn.select(folder)
    _, data = conn.search(None, "UNSEEN")
    ids = data[0].split()[-limit:]
    msgs = []
    for mid in ids:
        _, mdata = conn.fetch(mid, "(RFC822)")
        parsed = email_lib.message_from_bytes(mdata[0][1])
        body = ""
        if parsed.is_multipart():
            for part in parsed.walk():
                if part.get_content_type() == "text/plain":
                    body += part.get_payload(decode=True).decode(
                        "utf-8", "replace")
        else:
            body = parsed.get_payload(decode=True).decode("utf-8", "replace")
        msgs.append({
            "id": "imap-%s" % mid.decode(),
            "from": parsed.get("From", ""),
            "subject": parsed.get("Subject", ""),
            "body": body.strip()[:4000],
            "date": parsed.get("Date", ""),
        })
    conn.close()
    conn.logout()
    return msgs


# ---------------------------------------------------------------- state
def load_state():
    p = os.path.join(OUT, "state.json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {"processed": []}


def save_state(state):
    with open(os.path.join(OUT, "state.json"), "w") as f:
        json.dump(state, f, indent=2)


def log_run(entry):
    p = os.path.join(OUT, "runs.jsonl")
    with open(p, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------- matching
def _match(value, rule):
    if isinstance(rule, str):
        return rule.lower() in value.lower()
    if isinstance(rule, list):
        return any(r.lower() in value.lower() for r in rule)
    if isinstance(rule, dict):
        if "regex" in rule:
            return re.search(rule["regex"], value, re.I) is not None
    return False


def workflow_matches(wf, email):
    trig = wf.get("trigger", {})
    if trig.get("type") != "new_email":
        return False
    f = trig.get("filter", {})
    if "from" in f and not _match(email["from"], f["from"]):
        return False
    if "subject" in f and not _match(email["subject"], f["subject"]):
        return False
    if "body" in f and not _match(email["body"], f["body"]):
        return False
    return True


# ---------------------------------------------------------------- actions
def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def do_classify(email, brain, ctx):
    result, used = brain.classify(email)
    ctx["classification"] = result
    ctx["brains"]["classify"] = used
    return "classified as %s (%s) [%s]" % (result["label"], result["reason"], used)


def do_summarize(email, brain, ctx):
    summary, used = brain.summarize(email)
    ctx["summary"] = summary
    ctx["brains"]["summarize"] = used
    return "summarized [%s]" % used


def do_extract(email, brain, ctx, fields):
    data, used = brain.extract(email, fields)
    ctx["extracted"] = data
    ctx["brains"]["extract"] = used
    return "extracted %s [%s]" % (json.dumps(data)[:80], used)


def do_draft_reply(email, brain, ctx, wf_name):
    summary = ctx.get("summary") or brain.summarize(email)[0]
    draft, used = brain.draft_reply(email, summary)
    ctx["brains"]["draft_reply"] = used
    subject = email["subject"]
    subject = re.sub(r"^(re:\s*)+", "", subject, flags=re.I)  # avoid "Re: Re:"
    fname = "%s__%s.txt" % (now_iso().replace(":", "-"), _slug(email["subject"]))
    path = os.path.join(OUT, "drafts", fname)
    with open(path, "w") as f:
        f.write("To: %s\nSubject: Re: %s\nWorkflow: %s\nBrain: %s\nDate: %s\n\n%s\n"
                % (email["from"], subject, wf_name, used, now_iso(), draft))
    ctx["draft_path"] = path
    return "draft saved -> %s [%s]" % (os.path.basename(path), used)


def do_create_task(email, brain, ctx, template="{label}: {subject}"):
    label = ctx.get("classification", {}).get("label", "task")
    task = template.format(label=label.upper(), subject=email["subject"],
                           sender=email["from"],
                           summary=(ctx.get("summary") or "")[:140])
    path = os.path.join(OUT, "tasks.md")
    with open(path, "a") as f:
        f.write("- [ ] %s _(from %s, %s)_\n" % (task, email["from"], now_iso()))
    return "task added"


def do_append_digest(email, brain, ctx, digest_file="digest.md"):
    summary = ctx.get("summary") or brain.summarize(email)[0]
    path = os.path.join(OUT, "digest", digest_file)
    with open(path, "a") as f:
        f.write("\n## %s\n_%s — %s_\n\n%s\n" % (
            email["subject"], email["from"], now_iso(), summary))
    return "appended to %s" % digest_file


def do_append_csv(email, brain, ctx, csv_file, columns):
    data = ctx.get("extracted") or {}
    path = os.path.join(OUT, csv_file)
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        if new:
            w.writeheader()
        w.writerow({c: data.get(c, "") for c in columns})
    return "row appended to %s" % csv_file


STEP_HANDLERS = {
    "classify": lambda e, b, c, s: do_classify(e, b, c),
    "summarize": lambda e, b, c, s: do_summarize(e, b, c),
    "extract": lambda e, b, c, s: do_extract(e, b, c, s.get("fields", [])),
    "draft_reply": lambda e, b, c, s: do_draft_reply(e, b, c, s.get("_wf", "?")),
    "create_task": lambda e, b, c, s: do_create_task(
        e, b, c, s.get("template", "{label}: {subject}")),
    "append_digest": lambda e, b, c, s: do_append_digest(
        e, b, c, s.get("file", "digest.md")),
    "append_csv": lambda e, b, c, s: do_append_csv(
        e, b, c, s.get("file", "data.csv"), s.get("columns", [])),
}


# ---------------------------------------------------------------- engine
def run_once(inbox, workflows, brain, verbose=True):
    state = load_state()
    processed = set(state.get("processed", []))
    report = []

    for email in inbox:
        if email["id"] in processed:
            continue
        matched = [w for w in workflows if workflow_matches(w, email)]
        if not matched:
            # default: classify everything, always
            matched = [{"name": "_default_triage", "steps": [{"op": "classify"}]}]
        for wf in matched:
            ctx = {"brains": {}}
            step_notes = []
            for step in wf.get("steps", []):
                op = step["op"]
                handler = STEP_HANDLERS.get(op)
                if not handler:
                    step_notes.append("unknown op %s (skipped)" % op)
                    continue
                step = dict(step)
                step["_wf"] = wf["name"]
                try:
                    note = handler(email, brain, ctx, step)
                except Exception as ex:
                    note = "%s FAILED: %s" % (op, str(ex)[:120])
                step_notes.append("%s: %s" % (op, note))
            entry = {
                "ts": now_iso(), "email_id": email["id"],
                "workflow": wf["name"], "subject": email["subject"],
                "classification": ctx.get("classification"),
                "summary": ctx.get("summary"),
                "extracted": ctx.get("extracted"),
                "draft_path": ctx.get("draft_path"),
                "brains": ctx.get("brains"), "steps": step_notes,
            }
            log_run(entry)
            report.append(entry)
            if verbose:
                print("[%s] %s" % (wf["name"], email["subject"][:60]))
                for n in step_notes:
                    print("    - %s" % n)
        processed.add(email["id"])

    state["processed"] = sorted(processed)
    save_state(state)
    return report


def cmd_init(_args):
    for d in [OUT, os.path.join(OUT, "drafts"), os.path.join(OUT, "digest")]:
        os.makedirs(d, exist_ok=True)
    if not os.path.exists(os.path.join(OUT, "tasks.md")):
        with open(os.path.join(OUT, "tasks.md"), "w") as f:
            f.write("# OpenRelay tasks\n\n")
    print("initialized " + OUT)


def main():
    ap = argparse.ArgumentParser(description="OpenRelay — local AI email workflow agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create out/ directories")
    rp = sub.add_parser("run", help="process the inbox once")
    rp.add_argument("--inbox", required=True, help="JSON inbox file")
    rp.add_argument("--workflows", required=True, help="workflows JSON file")
    rp.add_argument("--brain", default="ollama", choices=["ollama", "heuristic"],
                   help="ollama tries the LLM first (falls back automatically)")
    rp.add_argument("--reset", action="store_true", help="clear processed state first")
    args = ap.parse_args()

    if args.cmd == "init":
        return cmd_init(args)

    cmd_init(args)
    if args.reset:
        save_state({"processed": []})
    with open(args.workflows) as f:
        workflows = json.load(f)["workflows"]
    inbox = load_json_inbox(args.inbox)
    print("inbox: %d messages | workflows: %d | brain: %s"
          % (len(inbox), len(workflows), args.brain))
    brain = Brain(prefer=args.brain)
    t0 = datetime.now()
    report = run_once(inbox, workflows, brain)
    dt = (datetime.now() - t0).total_seconds()
    ollama_steps = sum(1 for r in report for b in (r["brains"] or {}).values()
                       if b == "ollama")
    print("\ndone: %d emails processed through %d workflow runs in %.0fs "
          "(%d steps used the LLM brain)" % (len(inbox), len(report), dt, ollama_steps))


if __name__ == "__main__":
    main()
