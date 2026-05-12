# issue-companion

A local-LLM-powered microservice that nudges GitHub issues toward clarity before engineers see them — without ever blocking submission.

## Why this exists

PMs and customers file tickets in isolation. Engineers read them in aggregate. That gap quietly burns engineering time.

Three weeks ago, on a real production repo, two 🔴-critical issues were filed by the same person eight minutes apart:

- **Ticket A:** "Notifications keep popping up multiple times a day for the same alert — it's annoying." 🔴
- **Ticket B:** "Notifications aren't firing on this code path — users aren't being alerted." 🔴

Same notification code path. Filed without awareness of each other. By the time an engineer opened Ticket B, they had to mentally reconcile two contradictory feature requests, both marked critical, and figure out whether they were from the same customer (in which case the rule needs rethinking) or different customers (in which case both might be valid).

The engineer can spot the conflict in thirty seconds. The reporter, mid-day, juggling customer phone calls, cannot. That's the gap this tool tries to close.

## What it does

`issue-companion` watches a repository's issues. When a new issue is opened — or a substantial comment is added — it runs the text through a locally-hosted LLM with three jobs:

1. **Conflict detection.** Find open issues from the last N days that semantically contradict the new one. Surface them as a soft comment: *"This issue asks for X. Issue #243, also still open, asks for the opposite. Are these the same group of users, or different ones?"*

2. **Clarity check.** Flag vague language a reporter might not notice. *"You mentioned 'a lot' — would 'roughly N per hour' help the engineer triage faster?"* / *"You said the bug happens 'sometimes' — once a day, once a week, after a specific action?"*

3. **Missing-context check.** Using a small index of the repo's CLAUDE.md, recent commits, and the issue template's expected fields, gently note when a high-signal field was left blank. *"The template asks for app version — including it often saves a round trip."*

It posts as a single comment, signed by a configured bot account. The reporter is free to ignore it, edit the issue, or carry on.

## What it explicitly is *not*

- **Not a hard linter.** It never blocks issue creation, never enforces a checklist, never closes "low quality" tickets.
- **Not a triage bot.** It does not assign labels, milestones, or owners.
- **Not a nag.** It comments once per issue. It does not chase replies.
- **Not a code reviewer.** It reads the issue and a small slice of repo context; it does not analyze diffs.
- **Not a translator or rewriter.** Reporters' words stay theirs. The tool asks questions; it does not edit the issue body.

The bar for shipping a change is *"did this help an engineer spend less time clarifying tickets?"* — not *"did this enforce a policy?"*. The moment the tool starts feeling like a hall monitor, reporters stop reading its comments, and it's dead.

## Why local LLM

Bug reports often contain things that should not leave the host: customer names, license plates, internal user IDs, partner identifiers, screenshots with PII. Running inference locally — via Ollama or similar — keeps that data on the same network as the rest of the application's secrets. No third-party API calls, no audit-log surprises.

A side benefit: small local models (3B–8B parameter range) are entirely adequate for the kind of structured comparison this tool does. The bar is not "write good prose," it is "given two short ticket bodies, decide whether they conflict, and if so, draft one polite sentence."

## Architecture (planned)

- **Trigger:** GitHub webhook on `issues.opened` and `issue_comment.created`.
- **Service:** Single Go (or Python/FastAPI) binary, stateless, behind a reverse proxy.
- **Inference:** Ollama on the same host (or sidecar container).
- **Index:** pgvector or sqlite-vec, storing embeddings of recent issues + ingested repo context (CLAUDE.md, README, recent commit messages, issue template).
- **Output:** A single comment posted via the GitHub API as a configured bot account.
- **Idempotency:** Skip if a comment from the bot already exists on the same issue (no spam on edits).

## Status

Early. README only. The shape above is the hypothesis. The first cut will probably ship as just **conflict detection against the last 30 days of open issues** — the simplest piece, the one that would have caught the motivating example, and the one easiest to evaluate honestly. Clarity checks and missing-context checks come after that earns its keep.

## License

MIT.
