#!/usr/bin/env python3
"""issue-companion v0 — flag conflicts between a GitHub issue and recent open ones.

Usage:
    ./companion.py owner/repo issue_number

Reads the target issue, fetches the last N open issues in the repo, embeds each
with nomic-embed-text via local Ollama, picks the top-K most similar, then asks
llama3.2 whether each candidate pair describes contradictory requirements. Any
conflicts found are printed to stdout in a form ready to drop into a comment.

Authentication piggybacks on the local `gh` CLI — no token wiring needed.

Design notes
------------
The hypothesis we're testing is whether a *small* local LLM (3B params) is
good enough at the binary "do these two tickets pull in opposite directions"
task. If the answer is yes on the motivating case (sentry#243 vs #244, where
the same author filed contradictory feature requests 8 minutes apart), the
shape generalises and we can layer on a webhook server, a persistent vector
store, and per-issue idempotency. If the answer is no, we revisit the model
size before building any of the infrastructure.

There is deliberately no caching, queueing, or persistence in v0 — every run
re-embeds everything. ~30 issues × ~150 ms each is well under a second on a
warm host, and skipping a cache eliminates a whole class of stale-data bugs
while we're still figuring out the prompts.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from typing import Any

import requests

OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
CHAT_MODEL = "qwen2.5:14b"  # multilingual; llama3.2:3b couldn't reason about CJK conflicts


def gh_api(path: str) -> Any:
    """Call the gh CLI's pass-through API. Uses whatever auth the user already has."""
    proc = subprocess.run(
        ["gh", "api", path],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def fetch_issue(repo: str, number: int) -> dict:
    return gh_api(f"repos/{repo}/issues/{number}")


def fetch_recent_open_issues(repo: str, exclude: int, limit: int) -> list[dict]:
    # gh's /issues endpoint includes PRs; drop them and skip the target itself.
    raw = gh_api(
        f"repos/{repo}/issues?state=open&per_page={limit}"
        f"&sort=updated&direction=desc"
    )
    return [i for i in raw if "pull_request" not in i and i["number"] != exclude]


def embed(text: str) -> list[float]:
    r = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text[:4000]},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["embedding"]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def conflict_check(target: dict, candidate: dict) -> tuple[bool, str]:
    """Ask the LLM whether two issues describe contradictory requirements.

    Few-shot framing: small models miss "want more X" + "want less X on the
    same surface" without an explicit example. We give one positive and one
    negative example before asking about the real pair.
    """
    prompt = f"""You compare two GitHub issues from the same repo and decide if
they CONFLICT. A conflict means satisfying one would partially undo or contradict
the other — usually on the same code path, the same UX behaviour, or the same
quantity (one asks for MORE, the other asks for LESS).

Independent feature requests, or two reports of the same bug, are NOT conflicts.

Reply with strict JSON: {{"conflict": true | false, "reason": "<one sentence, under 30 words>"}}

Example 1 — CONFLICT:
  A: "Notifications fire too often, drivers complain — only fire once per day."
  B: "Drivers aren't being notified, every update should fire a push."
  → {{"conflict": true, "reason": "Same notification path: A wants fewer pushes per day, B wants more pushes per update — directly opposite asks."}}

Example 2 — NOT a conflict:
  A: "Login screen crashes on iOS 17."
  B: "Add dark-mode toggle to settings page."
  → {{"conflict": false, "reason": "Unrelated surfaces; one is an iOS login bug, the other is a settings-page feature."}}

Now classify these two real issues (any language):

Issue A — #{target["number"]} {target["title"]}
{(target.get("body") or "")[:1500]}

---

Issue B — #{candidate["number"]} {candidate["title"]}
{(candidate.get("body") or "")[:1500]}

JSON reply:"""
    r = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": CHAT_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
        },
        timeout=120,
    )
    r.raise_for_status()
    raw = r.json()["response"]
    try:
        parsed = json.loads(raw)
        return bool(parsed.get("conflict")), str(parsed.get("reason", "")).strip()
    except json.JSONDecodeError:
        return False, f"(could not parse LLM reply: {raw[:200]!r})"


def issue_text(i: dict) -> str:
    body = (i.get("body") or "")[:2000]
    return f"{i['title']}\n\n{body}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("repo", help="owner/repo, e.g. ryanalexmartin/sentry")
    p.add_argument("number", type=int, help="issue number to check")
    p.add_argument("--top-k", type=int, default=5, help="how many nearest candidates to LLM-check")
    p.add_argument("--limit", type=int, default=30, help="how many recent open issues to scan")
    args = p.parse_args()

    target = fetch_issue(args.repo, args.number)
    print(f"target: #{target['number']} — {target['title']}", file=sys.stderr)

    candidates = fetch_recent_open_issues(args.repo, exclude=args.number, limit=args.limit)
    print(f"scanning {len(candidates)} other open issues", file=sys.stderr)

    target_vec = embed(issue_text(target))
    scored: list[tuple[float, dict]] = []
    for c in candidates:
        v = embed(issue_text(c))
        scored.append((cosine(target_vec, v), c))
    scored.sort(key=lambda t: t[0], reverse=True)

    top = scored[: args.top_k]
    print(f"top-{len(top)} candidates by embedding similarity:", file=sys.stderr)
    for s, c in top:
        print(f"  {s:.3f}  #{c['number']:<5d}  {c['title']}", file=sys.stderr)

    conflicts: list[tuple[dict, str, float]] = []
    for sim, c in top:
        is_conflict, reason = conflict_check(target, c)
        marker = "CONFLICT" if is_conflict else "ok"
        print(f"  [{marker:8s}] #{c['number']:<5d} {reason}", file=sys.stderr)
        if is_conflict:
            conflicts.append((c, reason, sim))

    if not conflicts:
        print("No conflicts found.")
        return 0

    print(f"\n## Possible conflicts with #{target['number']}\n")
    for c, reason, sim in conflicts:
        print(f"- **#{c['number']}** {c['title']} _(similarity {sim:.2f})_")
        print(f"    {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
