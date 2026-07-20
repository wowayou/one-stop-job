# Privacy Boundary

Status: active
Updated: 2026-07-20
Intended user: self-hosted, single-user deployments

## Operating boundary

This public repository contains application code, blank default profile fields, and synthetic fixtures only. Personal context remains in a separate local directory selected through `JOB_ONE_STOP_CONTEXT_REPO_PATH`; the application reads that directory through an explicit allowlist.

Decision-chat text and analysis runs are stored in the local SQLite database. Uploaded screenshots are stored under the configured data directory in `chat_attachments/`; only attachment metadata is stored in SQLite. Neither location belongs in Git.

Never commit or publish:

- `.env` files or API credentials;
- SQLite databases, logs, exports, browser profiles, cookies, or session state;
- resumes, chat transcripts, personal profiles, job cards, outreach history, or company notes;
- screenshots containing real job-pipeline data or local filesystem paths.

## Deployment boundary

The application has no account system or multi-user authorization. Keep it bound to localhost or behind a private authenticated access layer. Do not mount a personal context directory into a publicly reachable deployment.

When AI is enabled, the submitted message, recent thread messages, selected job facts, allowlisted decision rules/profile/board content, and the current screenshot (if any) are sent to the configured model provider. Review that provider's privacy and retention terms. Disable AI when material should remain entirely local; the deterministic rule pass will still run.

## Risks and failure modes

- Git history can retain deleted content after a normal commit.
- Forks, clones, caches, release assets, CI logs, and screenshots can preserve previously published data.
- Environment variables can leak through build logs or diagnostic endpoints even when `.env` is ignored.
- Archive exports contain chat text and analysis results (but not screenshot bytes) and may contain personal data even though source code is clean.

Before publishing, inspect the full diff, tracked files, commit-author email, build context, and generated artifacts. Use a GitHub noreply commit address when the author email should remain private.
