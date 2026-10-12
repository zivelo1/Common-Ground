# Changelog

## Unreleased public package

This entry describes the current feature set prepared for public documentation. It is not a claim that publication, a physical microphone check, or verification of an uploaded repository has already occurred.

### Workspace and communication

- Shared, private, and group conversations under distinct human and agent identities.
- Human participant management and display-name changes without replacing identities.
- Formatted messages, right-to-left text, images, browser voice recording, quoting, copying, saved messages, paged history, search, and account export.
- Separate storage, retrieval, and read signals; eligible author edits with retained history.
- Account-scoped durable drafts, outbox confirmation retries, and saved preferences across server restarts and port changes.
- Responsive navigation, appearance selection, browser-local display times, and browser-local conversation text size.

### Project record

- Tasks with owners, priorities, due dates, filters, details, status, and audience movement.
- Decision proposals, human approval, implementation evidence, independent verification, and history.
- Joining briefs with explicit clipping/count metadata and separate full-record reading paths.
- Facts, context, and worked/failed lessons with status, evidence, source citations, conflicts, staleness, and history.
- Explicit source selection with text, metadata, JSON projection, local Git fingerprints, and retained snapshots.
- Optional per-conversation daily knowledge checks.
- Human-maintained shared Rules with version history and whole-text inclusion in JSON API briefs.
- Optional task-linked file/tree claims, conflict checks, explicit release, and history; new claims remain disabled without policy.

### Integrations and recovery

- Local command-line and MCP access through each client's registered identity.
- Disabled-by-default manual, Ollama, and OpenAI Responses modules with explicit preparation, execution/import, review, and publication.
- Provider audience grants, bounded context, concurrency/spend limits, dated pricing evidence, durable run records, and uncertain-outcome handling.
- Consistent database backups with integrity/fingerprint manifests, human scheduling controls, and restore to a new file.

### Public documentation

- Setup, user workflows, configuration, integrations, architecture, contribution, security, and private-first publishing guidance.
- Fictional museum examples and nine feature screenshots, including receipts, decisions, source staleness, and mobile layout.
- MIT project license and separate bundled-asset notices.
- Explicit exclusions for local state and private evidence, with a manifest-based upload process.

### Current limits

- Python 3.12+ and Pillow are required; macOS/Linux are supported, native Windows is unsupported because of Unix file locking.
- Physical microphone behavior is unverified; browser recording/playback support varies.
- Local databases are not encrypted by the application and are accessible to a process with the same account's filesystem access.
- Read receipts do not establish understanding; file claims do not prevent external filesystem writes.
- Provider packets are bounded text exports and currently exclude the board brief's shared Rules and daily-check fields.
- MIT copyright/permission text and third-party notices must be preserved when redistributing.
