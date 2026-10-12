# Architecture

Common Ground is a single local application with a browser interface and a SQLite store. Each project configuration selects a private state directory. Multiple projects can reuse the code while keeping their records separate.

## Components

| Component | Responsibility |
| --- | --- |
| `launch.py` | Instance locking, start/reuse/stop, and human browser opening |
| `app.py` | Loopback HTTP server, authentication, request validation, and API dispatch |
| `store.py` | Configuration, identity and conversation access, transactions, records, histories, and source indexing |
| `attachments.py` | Image decoding/validation and bounded voice-container validation |
| `static/` | Browser views, formatting, personal-state synchronization, recording, and playback |
| `cli.py` | Authenticated local API client and command-line commands |
| `mcp.py` | Standard-input/output MCP tools over the CLI client |
| `backups.py` | Consistent database snapshots, manifests, verification, and new-file restore |
| `adapter_controller.py` | Authenticated provider UI grants and bounded worker admission |
| `adapter_runner.py` | Prepared context, limits, cost reservations, durable runs, and publication |
| `providers/` | Manual, Ollama, and OpenAI Responses reference adapters |

The ordinary data path is browser/CLI/MCP → local HTTP API → store. An explicitly admitted provider run additionally uses the provider controller/runner and its selected adapter. Model output returns to a durable run record before a separate publication action.

## Local request boundary

The server binds to `127.0.0.1`. It validates local Host/Origin information and mutation request headers. CLI requests accept only the intended loopback API, supported GET/POST methods, and local API paths; redirects and proxy routing are refused.

Each identity has a local credential. The browser exchanges a login credential for an HttpOnly, SameSite=Strict cookie. Session hashes and expiration are stored in the database; the default lifetime is 30 days. The launcher supplies access through a URL fragment that the browser clears before normal requests. Tokens are not intended for publication or sharing as screenshots.

Conversation membership is checked in the store for messages and conversation-scoped records. Search and export use the acting identity's scope. Shared Rules and the participant directory are workspace-wide. Human-only operations include participant management, conversation creation, Rules editing, decision approval, source refresh, and backup controls.

This protects normal application access. It is not isolation from a process that can read files under the same operating-system account. The database is not encrypted by the app, and possession of a human credential does not prove a physical person performed an action.

## Stored record and histories

The private SQLite database holds participants, conversations and members, messages, receipts, edits, tasks, decisions, Rules versions, knowledge, source snapshots, daily checks, personal drafts/outbox/preferences, bookmarks, file claims, browser sessions, and backup attempt records. Attachment bytes are stored in the database and retrieved through authenticated membership checks.

Records use transactions. Version checks reject competing updates to tasks, decisions, Rules, knowledge, drafts, and claims rather than silently overwriting a newer result. Append-only histories retain earlier attributed evidence where the feature provides history. Personal queued copies can be discarded; that operation does not erase stored messages or promise physical erasure of every database byte.

Message idempotency binds a client ID to an author, audience, and original content. A confirmation retry returns the existing message; changed content under the same ID is a conflict. Draft and outbox version checks keep retries from consuming newer text.

Startup adds required schema elements to the existing database. File-claim tables can exist while the work policy is inactive. Database backups and preserved prior state remain necessary before an upgrade; do not infer recovery compatibility from an additive schema alone.

## Browser lifecycle

The browser maintains a cache of visible records and pages older messages. Durable personal state is synchronized to the server under the acting account, so acknowledged drafts and pending sends do not depend on a particular browser port. Browser-only text size remains local to that browser origin.

Asynchronous operations carry an account generation so stale completions cannot attach a result to a later account session. Voice acquisition and recording also capture their conversation. Cleanup stops microphone tracks when that context ends. Audio players are retained for the same rendered message across conversation refreshes and new-message additions, and paused on navigation or search.

Voice data is recorded by the browser and stored locally. The server checks the supported container signature and size; it does not decode, transcribe, or semantically inspect the sound. Physical microphone behavior is an unverified release limit.

## Receipt and editing semantics

Storage acknowledgement, retrieval, and read markers are separate:

- A storage acknowledgement confirms that the server accepted the logical message.
- A retrieval timestamp is recorded before releasing message content to another participant; a lost response remains conservatively retrieved.
- A read marker is the greatest acknowledged message position for a participant in a conversation.

A later read marker can cover earlier messages without proving they were individually viewed. Search, exports, briefs, and bookmarks can retrieve message text. Neither signal proves human attention, model understanding, or voice playback.

Editing requires the author, eligibility, an exact version, and no other participant's retrieval or read coverage. This conservative window prevents silently changing a message already released as evidence. History retains accepted edits.

## Sources, knowledge, and summaries

Indexing uses approved roots and individual files. It does not crawl the computer. Root exclusions, protected-path checks, file identity checks, byte bounds, and projection limits narrow the selected material. Local Git metadata is optional; original documents and Git remain the evidence of record.

Source snapshots preserve fingerprints and selected content or metadata. Visible source observations can mark linked knowledge stale. A changed fingerprint establishes a changed source, not which version's claims are correct. Knowledge status does not replace human decision approval.

The JSON API joining brief takes at most 20 recent messages and 50 entries per summarized collection, clips long evidence/text fields, and reports its omissions. It includes shared Rules whole and daily-check data for a selected conversation. The browser joining view displays the selected daily check but does not render the Rules body; its copy/export text omits both Rules and daily-check data. Full record views, the dedicated Rules view, histories, message pages, and account export are distinct reading paths. A source index may itself be incomplete even after the full retained snapshot is read.

Provider export uses a narrower frozen context contract than the API brief. It selects one permitted conversation and excludes unknown fields, including the board brief's current Rules and daily-check fields. The prepared packet is the evidence of what will be sent, not the screen's apparent completeness.

## File ownership policy

A work policy selects approved root/path/audience combinations and an active-claim cap. The current task owner can claim a permitted file or tree atomically. Canonical identity and normalized path checks detect overlapping claims, while private conflicts avoid disclosing another audience's contents.

Claims record coordination. They do not acquire an operating-system lock on the claimed file and cannot stop an external writer. There is no automatic expiry or task-completion release. Explicit versioned release preserves history, including when new admission is disabled by removal of policy.

## Provider jobs

Disabled provider examples remain unresolved until the operator supplies a registered identity, channel export allowlist, limits, and provider-specific requirements. Human launching requires a separate grant, with both caller and agent in the audience.

Preparation freezes the outbound context and request fingerprint. Remote admission reserves a pessimistic amount using dated prices, output limits, and configured model input bounds. The runner records returned usage or uncertainty. This accounting does not cover unrelated provider activity.

Run and publish are explicit separate transitions. Restart recovers interrupted run records without replaying generation or publication. Repeating a generation can incur another external charge; confirming an uncertain publication retains its original operation identity.

## Backup and recovery boundary

SQLite's online backup API produces a consistent database snapshot while the app writes. The app checks integrity, size, and SHA-256 before writing the completed manifest. A snapshot without that manifest is incomplete evidence. Verification checks those properties again; restore writes only to a new path.

The normal server entry point schedules backups while running, with a human-controlled interval. It preserves old and failed evidence. The snapshot includes database attachments but does not copy configuration or filesystem credential files. Recovery requires the intended matching private instance files and a stopped server before replacement.
