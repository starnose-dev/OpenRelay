# OpenRelay — the free, local-first AI email workflow agent

**Relay.app shut down on September 14, 2026.** It was the AI workflow-automation
tool ("the new Zapier"): AI agents that watched your inbox and turned email into
triage, summaries, drafts, tasks, and structured data. When it died, thousands of
workflows died with it — and the "alternatives" all want a subscription.

OpenRelay is a clean-room rebuild of that lost core capability. It runs on your
own machine, uses a local LLM (Ollama — free, no API keys), and never sends your
email anywhere. Zero cost. Yours forever.

## What it does

You describe workflows in `workflows.json`:

```json
{
  "name": "vip-deal-triage",
  "trigger": {"type": "new_email", "filter": {"from": ["acmecorp.com"]}},
  "steps": [{"op": "classify"}, {"op": "summarize"},
            {"op": "draft_reply"}, {"op": "create_task"}]
}
```

Available triggers: `new_email` (JSON file inbox for demo/tests, or real IMAP).
Available steps (each runs on the LLM brain, with automatic heuristic fallback):

| op | does what |
|----|-----------|
| `classify` | urgent / important / fyi / noise + reason |
| `summarize` | 2-sentence summary |
| `extract` | pull named fields (vendor, amount, dates…) as JSON |
| `draft_reply` | write a reply draft to `out/drafts/` |
| `create_task` | append to `out/tasks.md` |
| `append_digest` | collapse newsletters into `out/digest/digest.md` |
| `append_csv` | structured rows into a CSV (expenses, leads…) |

Everything is idempotent (processed IDs in `out/state.json`) and every run is
logged to `out/runs.jsonl` with which brain handled each step.

## Quickstart

```bash
python3 relay_agent.py init
# demo: 5-email founder inbox through 4 workflows
./demo.sh
# your own inbox file + workflows
python3 relay_agent.py run --inbox my_inbox.json --workflows workflows.json --reset
# real IMAP: python3 -c "from relay_agent import load_imap_inbox, ..."
# force the no-LLM heuristic brain:
python3 relay_agent.py run --inbox inbox_sample.json --workflows workflows.json --brain heuristic
```

Requirements: Python 3.8+ stdlib only. The LLM brain shells to
`~/workspace/bin/yoga-ssh` → local Ollama (`qwen3:8b`). Point `YOGA_SSH` /
`OLLAMA_MODEL` in `brain.py` at any Ollama host to relocate it.

## Why this exists

Relay's founder took the team to Google. The users got an export link and a
countdown. This is the other path: the capability, rebuilt in the open, running
locally, free forever. If you lost workflows on Sept 14, bring your Relay export
— the workflow JSON maps 1:1 to `workflows.json` concepts (trigger → steps).

*Clean-room rebuild: no Relay code, branding, or assets were used or copied.*
