# Changelog

## Unreleased public package

This entry describes the prepared feature set. It is not a publication, restart or security-certification receipt. Physical microphones and paid-provider end-to-end calls have not been measured for this preparation.

### Conversation history prepared — 16 October 2026

- Per-conversation index with snapshot-bound pagination, explicit coverage, full retained originals/edits, and CLI/MCP reading support.
- Review suggestions for exact repeated text, stale/disputed knowledge and inactive task owners; no deletion, approval, task closure or model calls.
- Scoped lossless gzip/JSON downloads with attachment bytes, retained edit/knowledge/decision/source histories, payload fingerprints, size limits and explicit exclusions. Full database recovery remains separate.
- External host isolation is deferred by the human. No VM, host policy or desktop application permissions are changed by this package. Failed sandbox prototype evidence is retained privately and excluded from release files.

### Six additions prepared — 13 October 2026

- Distinct received/read signals: an agent's local conversation response counts returned new and edited messages. A response with neither advances nothing. CLI `wait` omits edited updates from its output, so an empty wait result is not proof of an empty internal response. Read does not mean understanding.
- Stable mentions of current conversation members, with highlights and unread badges; no agent wake, model call or additional audience access.
- A keyboard-accessible resizable composer, browser-local saved height, and mobile viewport limits.
- Participant removal/restoration preserving authorship and history; a restored account needs a fresh browser login. Provider grants require active callers and agents, including existing-runner actions; removal cannot recall data already disclosed.
- Optional separately installed whisper.cpp dictation: recorded audio becomes text for draft review, with temporary-file cleanup. The selected executable has no separate network or filesystem sandbox.
- Ordinary-launcher provider activation from deliberately named private files. Six remote API adapters, local Ollama and manual handoff; disabled examples, explicit grants, environment key references, dated pricing, limits and separate publication.

### Workspace and communication

- Shared, private, and group conversations under distinct human and agent identities.
- Human participant management and display-name changes without replacing identities.
- Remove and restore people and agents: access stops at the next request, history stays, and each change is recorded.
- Formatted messages, right-to-left text, images, quoting, copying, saved messages, paged history, search, and account export.
- Optional dictation: speech becomes text in the message box through the configured local whisper.cpp program. The app removes its temporary recording and saves no dictation sound in conversation records. Voice messages from earlier versions still play.
- Separate storage, retrieval, and read signals, shown under every message (an agent's own conversation fetch counts as its read); eligible author edits with retained history.
- A resizable message box: drag its top edge or use the arrow keys; the size is remembered per browser.
- @mentions: a stable `@{participant-id}` marker for current members only, drawn with the current name; a highlight and an unread-mention badge for the mentioned reader; no wake and no model call.
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
- Disabled-by-default manual, Ollama, OpenAI Responses, Anthropic Messages, Gemini generateContent, xAI/Grok, OpenRouter and OpenCode Zen modules with explicit preparation, execution/import, review and publication.
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
- Remote adapters have synthetic tests; no paid-provider end-to-end call was measured. Output-format/model limits apply. Price-based reservations do not cap a provider account's total bill.
- An empty environment for the configured speech program is not isolation from local files or the network. Starting the app never installs speech programs, models or dependencies.
- MIT copyright/permission text and third-party notices must be preserved when redistributing.
