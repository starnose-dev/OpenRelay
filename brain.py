"""Brains for OpenRelay: an Ollama-backed LLM brain (via Yoga SSH, zero budget)
plus a deterministic heuristic fallback so the agent always works end-to-end.

The LLM brain shells out to ~/workspace/bin/yoga-ssh, which reaches the Yoga's
local Ollama (qwen3:8b). No cloud, no API keys, no cost.
"""
import base64
import json
import os
import re
import subprocess

YOGA_SSH = os.path.expanduser("~/workspace/bin/yoga-ssh")
OLLAMA_MODEL = "qwen3:8b"
TIMEOUT = 180


def _ollama_via_ssh(prompt, num_predict=420):
    """One Ollama /api/generate call through yoga-ssh. Returns raw text."""
    prompt_b64 = base64.b64encode(prompt.encode("utf-8")).decode("ascii")
    ps = (
        "$promptB64 = '" + prompt_b64 + "';"
        "$prompt = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($promptB64));"
        "$body = @{model='" + OLLAMA_MODEL + "';prompt=$prompt;stream=$false;think=$false;"
        "options=@{num_predict=" + str(num_predict) + ";temperature=0.2}} "
        "| ConvertTo-Json -Depth 6 -Compress;"
        "$r = Invoke-RestMethod -Uri http://127.0.0.1:11434/api/generate "
        "-Method Post -Body ([Text.Encoding]::UTF8.GetBytes($body)) "
        "-ContentType 'application/json' -TimeoutSec 150;"
        "[Console]::Out.Write($r.response)"
    )
    enc = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    out = subprocess.run(
        [YOGA_SSH, "powershell -NoProfile -EncodedCommand " + enc],
        capture_output=True, text=True, timeout=TIMEOUT,
    )
    if out.returncode != 0:
        raise RuntimeError("yoga-ssh failed: " + out.stderr[:300])
    text = out.stdout.strip()
    if not text:
        raise RuntimeError("empty Ollama response")
    return text


def _json_from(text):
    """Defensively extract the first {...} JSON object from model output."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON in model output: " + text[:200])
    return json.loads(m.group(0))


# ---------------------------------------------------------------- heuristic
_URGENT = ["urgent", "asap", "today", "deadline", "broken", "down",
           "can't log in", "cannot log in", "outage", "breach", "signature"]
_IMPORTANT = ["contract", "agreement", "invoice", "payment", "pilot",
              "investor", "board", "deadline", "renewal"]
_NOISE = ["unsubscribe", "exciting opportunity", "10x your", "guaranteed",
          "crypto", "viagra", "lottery"]


def _h_classify(email):
    sender = email.get("from", "").lower()
    if any(w in sender for w in ("newsletter", "digest", "substack", "brew")):
        return {"label": "fyi", "reason": "newsletter sender"}
    blob = (email.get("subject", "") + " " + email.get("body", "")).lower()

    def _any(words):
        return any(re.search(r"\b" + re.escape(w) + r"\b", blob) for w in words)

    if _any(_NOISE):
        return {"label": "noise", "reason": "matches spam/unsolicited pattern"}
    if _any(_URGENT):
        return {"label": "urgent", "reason": "time-sensitive or breakage language"}
    if _any(_IMPORTANT):
        return {"label": "important", "reason": "business-critical topic"}
    return {"label": "fyi", "reason": "informational, no action language"}


def _h_summarize(email):
    body = email.get("body", "").strip()
    sents = re.split(r"(?<=[.!?])\s+", body)
    sents = [s for s in sents if len(s) > 20][:2]
    return " ".join(sents) or body[:200]


def _h_extract(email, fields):
    blob = email.get("subject", "") + "\n" + email.get("body", "")
    out = {}
    for f in fields:
        fl = f.lower()
        if fl in ("amount", "total", "price"):
            m = re.search(r"\$\s?[\d,]+(?:\.\d{2})?", blob)
            out[f] = m.group(0) if m else ""
        elif fl in ("vendor", "from", "company"):
            out[f] = email.get("from", "")
        elif fl in ("date", "due", "due_date"):
            m = re.search(
                r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:,\s*\d{4})?",
                blob, re.I)
            out[f] = m.group(0) if m else ""
        else:
            out[f] = ""
    return out


def _h_draft(email, summary):
    return (
        "Hi,\n\nThanks for your email about \"%s\".\n\n"
        "Quick summary of where things stand: %s\n\n"
        "I'll follow up with details shortly — wanted to acknowledge this right away.\n\n"
        "Best regards" % (email.get("subject", ""), summary)
    )


# ------------------------------------------------------------------ public
class Brain:
    """Tries the Ollama LLM brain first, falls back to heuristics per call.

    Every method returns (result_dict, brain_used) where brain_used is
    "ollama" or "heuristic". Nothing ever raises: worst case we degrade.
    """

    def __init__(self, prefer="ollama"):
        self.prefer = prefer
        self._ollama_ok = None

    # -- internal ----------------------------------------------------
    def _try_llm(self, prompt, num_predict=420):
        if self.prefer != "ollama":
            raise RuntimeError("llm not preferred")
        if self._ollama_ok is False:
            raise RuntimeError("ollama previously failed")
        try:
            text = _ollama_via_ssh(prompt, num_predict)
            self._ollama_ok = True
            return text
        except Exception as e:
            self._ollama_ok = False
            raise RuntimeError(str(e)[:200])

    # -- classify ----------------------------------------------------
    def classify(self, email):
        prompt = (
            "You triage emails for a busy founder. Classify this email.\n"
            "IMPORTANT: unsolicited recruiting spam, get-rich-quick pitches, "
            "'no interview needed' offers, and anything with an unsubscribe "
            "footer pushing a product is 'noise' — never urgent, no matter "
            "how exciting it sounds.\n"
            "From: %s\nSubject: %s\nBody:\n%s\n\n"
            "Reply with ONLY strict JSON: "
            '{"label": "urgent|important|fyi|noise", "reason": "short reason"}'
            % (email.get("from"), email.get("subject"),
               email.get("body", "")[:1500])
        )
        try:
            data = _json_from(self._try_llm(prompt, 160))
            if data.get("label") in ("urgent", "important", "fyi", "noise"):
                return data, "ollama"
        except Exception:
            pass
        return _h_classify(email), "heuristic"

    # -- summarize ---------------------------------------------------
    def summarize(self, email):
        prompt = (
            "Summarize this email in 2 sentences max. No preamble, just the summary.\n"
            "From: %s\nSubject: %s\nBody:\n%s"
            % (email.get("from"), email.get("subject"),
               email.get("body", "")[:2000])
        )
        try:
            return self._try_llm(prompt, 200).strip(), "ollama"
        except Exception:
            pass
        return _h_summarize(email), "heuristic"

    # -- extract -----------------------------------------------------
    def extract(self, email, fields):
        prompt = (
            "Extract these fields from the email as strict JSON (empty string if absent). "
            "Fields: %s\nFrom: %s\nSubject: %s\nBody:\n%s\n\nReply with ONLY the JSON object."
            % (", ".join(fields), email.get("from"), email.get("subject"),
               email.get("body", "")[:2000])
        )
        try:
            data = _json_from(self._try_llm(prompt, 220))
            return {f: str(data.get(f, "")) for f in fields}, "ollama"
        except Exception:
            pass
        return _h_extract(email, fields), "heuristic"

    # -- draft_reply -------------------------------------------------
    def draft_reply(self, email, summary, sender="Founder"):
        prompt = (
            "Draft a short, warm, professional reply to this email (under 120 words). "
            "Sign off as '%s' — plain text, no brackets or placeholders. "
            "No subject line, just the body.\n"
            "From: %s\nSubject: %s\nSummary: %s\nBody:\n%s"
            % (sender, email.get("from"), email.get("subject"), summary,
               email.get("body", "")[:1500])
        )
        try:
            return self._try_llm(prompt, 300).strip(), "ollama"
        except Exception:
            pass
        return _h_draft(email, summary), "heuristic"
