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
| `branding.py` | Validated optional instance name/color/monogram and escaped local HTML/SVG presentation |
| `cli.py` | Authenticated local API client and command-line commands |
| `mcp.py` | Standard-input/output MCP tools over the CLI client |
| `backups.py` | Consistent database snapshots, manifests, verification, and new-file restore |
| `adapter_controller.py` | Authenticated provider UI grants and bounded worker admission |
| `adapter_runner.py` | Prepared context, limits, cost reservations, durable runs, and publication |
| `providers/` | Six remote adapters, local Ollama, and manual text handoff |
| `dictation.py` | Optional WAV validation and a bounded call to the configured local speech program |

The ordinary data path is browser/CLI/MCP → local HTTP API → store. An explicitly admitted provider run additionally uses the provider controller/runner and its selected adapter. Model output returns to a durable run record before a separate publication action.

## Local request boundary

The server binds to `127.0.0.1`. It validates local Host/Origin information and mutation request headers. CLI requests accept only the intended loopback API, supported GET/POST methods, and local API paths; redirects and proxy routing are refused.

Each identity has a local credential. The browser exchanges a login credential for an HttpOnly, SameSite=Strict cookie. Session hashes and expiration are stored in the database; the default lifetime is 30 days. The launcher supplies access through a URL fragment that the browser clears before normal requests. Tokens are not intended for publication or sharing as screenshots.

Conversation membership is checked in the store for messages and conversation-scoped records. Search and export use the acting identity's scope. Shared Rules and the participant directory are workspace-wide. Human-only operations include participant management, conversation creation, Rules editing, decision approval, source refresh, and backup controls.

Participants are marked inactive rather than deleted. Removal and restoration preserve identity and history. Removal deletes browser sessions; restoration keeps credentials but needs a fresh browser login. Provider permission checks require an active agent and an active caller, including when a caller uses an existing runner. These checks do not recall outbound context already sent or stop unrelated processes.

This protects normal application access. It is not isolation from a process that can read files under the same operating-system account. The database is not encrypted by the app, and possession of a human credential does not prove a physical person performed an action.

## Stored record and histories

The private SQLite database holds participants, conversations and members, messages, receipts, edits, tasks, decisions, Rules versions, knowledge, source snapshots, daily checks, personal drafts/outbox/preferences, bookmarks, file claims, browser sessions, and backup attempt records. Attachment bytes are stored in the database and retrieved through authenticated membership checks.

Records use transactions. Version checks reject competing updates to tasks, decisions, Rules, knowledge, drafts, and claims rather than silently overwriting a newer result. Append-only histories retain earlier attributed evidence where the feature provides history. Personal queued copies can be discarded; that operation does not erase stored messages or promise physical erasure of every database byte.

Message idempotency binds a client ID to an author, audience, and original content. A confirmation retry returns the existing message; changed content under the same ID is a conflict. Draft and outbox version checks keep retries from consuming newer text.

Startup adds required schema elements to the existing database. File-claim tables can exist while the work policy is inactive. Database backups and preserved prior state remain necessary before an upgrade; do not infer recovery compatibility from an additive schema alone.

## Browser lifecycle

The browser maintains a cache of visible records and pages older messages. Durable personal state is synchronized to the server under the acting account, so acknowledged drafts and pending sends do not depend on a particular browser port. Browser-only text size and composer height remain local to that browser origin. The resize grip supports dragging, arrow keys and reset; viewport limits keep conversation space visible on mobile. These settings are separate from database-backed drafts.

Asynchronous operations carry an account generation so stale completions cannot attach a result to a later account session. Voice acquisition and recording also capture their conversation. Cleanup stops microphone tracks when that context ends. Audio players are retained for the same rendered message across conversation refreshes and new-message additions, and paused on navigation or search.

Dictation audio is recorded by the browser, decoded and resampled there to 16 kHz mono 16-bit WAV, and sent to the local server. The server checks the format, length and loudness, runs the configured local `whisper-cli` without a shell and with an empty environment, removes the temporary file after the attempt, and returns text for draft review. It stores no dictation audio in conversation records. The operator installs and selects the executable/model separately. There is no network or filesystem sandbox around that executable; its own behavior remains outside these application checks. Voice messages stored by earlier versions are still served to their conversation's members. Physical microphone behavior is an unverified release limit.

## Receipt and editing semantics

Storage acknowledgement, retrieval, and read markers are separate:

- A storage acknowledgement confirms that the server accepted the logical message.
- A retrieval timestamp is recorded before releasing message content to another participant; a lost response remains conservatively retrieved.
- A read marker is the greatest acknowledged message position for a participant in a conversation. A person's browser advances it while the conversation is on screen; an agent's local conversation response advances it to the greatest message ID in its new messages or edited-message updates. A response with neither advances nothing.

CLI `wait` reads the local conversation response but leaves edited-message updates out of its outward output. It can therefore return an empty new-message list after the server recorded an edited message as read. The marker describes the server-response boundary, not the completeness of a client's display. A later read marker can cover earlier messages without proving they were individually viewed. Search, exports, briefs, and bookmarks can retrieve message text. Neither signal proves human attention, model understanding, or voice playback.

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

Disabled provider examples remain unresolved until the operator supplies a registered identity, conversation export allowlist, limits, and provider-specific requirements. Human launching requires a separate grant, with both caller and agent active and in the audience. The ordinary launcher passes the deliberately named private files when present; it does not generate a key, install a dependency, or fill missing activation settings.

The reviewed registry contains OpenAI Responses, Anthropic Messages, Gemini generateContent, and Chat Completions entries for xAI/Grok, OpenRouter and OpenCode Zen. Their remote endpoints are fixed per entry. Local Ollama uses an explicit loopback chat endpoint; manual handoff calls no model. All are text-only. Synthetic transports exercise the remote formats; paid-provider end-to-end operation was not measured for this preparation.

Preparation freezes the outbound context and request fingerprint. Remote admission reserves a pessimistic amount using dated prices, output limits, and configured model input bounds. The runner records returned usage or uncertainty. This accounting does not cover unrelated provider activity or establish the external account's final bill. Client errors other than 408 are classified as failed; timeouts, server errors and lost connections can leave usage and billing unknown.

Run and publish are explicit separate transitions. Restart recovers interrupted run records without replaying generation or publication. Repeating a generation can incur another external charge; confirming an uncertain publication retains its original operation identity.

## Backup and recovery boundary

SQLite's online backup API produces a consistent database snapshot while the app writes. The app checks integrity, size, and SHA-256 before writing the completed manifest. A snapshot without that manifest is incomplete evidence. Verification checks those properties again; restore writes only to a new path.

The normal server entry point schedules backups while running, with a human-controlled interval. It preserves old and failed evidence. The snapshot includes database attachments but does not copy configuration or filesystem credential files. Recovery requires the intended matching private instance files and a stopped server before replacement.
