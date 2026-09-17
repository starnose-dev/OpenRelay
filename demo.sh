#!/usr/bin/env bash
# OpenRelay end-to-end demo: Relay.app died Sept 14, 2026.
# This runs the clean-room rebuild against a sample founder inbox.
set -e
cd "$(dirname "$0")"

echo "=== OpenRelay demo: AI email-workflow agent (post-Relay.app rebuild) ==="
echo ""
python3 relay_agent.py run --inbox inbox_sample.json --workflows workflows.json --reset --brain ollama

echo ""
echo "==================== DEMO RESULTS ===================="
echo ""
echo "--- tasks.md ---"
cat out/tasks.md
echo ""
echo "--- digest.md ---"
cat out/digest/digest.md
echo ""
echo "--- expenses.csv ---"
cat out/expenses.csv
echo ""
echo "--- reply drafts ---"
ls out/drafts/
echo ""
echo "--- first draft (deal email) ---"
head -20 "$(ls out/drafts/*pilot-agreement* | head -1)"
echo ""
echo "--- run log (last workflow run) ---"
tail -1 out/runs.jsonl | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps({k:d[k] for k in ('workflow','subject','classification','summary','brains')}, indent=2))"
