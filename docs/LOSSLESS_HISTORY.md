# Concise discussion with lossless history

Status: design for review. This preparation adds no automatic moderation, model call, scheduler, service, migration or message-deletion path. The retained record stays authoritative.

## What exists today

The real store preserves messages and accepted edits, participant identities and rename/removal history, knowledge versions, decision histories and selected source snapshots. Knowledge can name sources, conflicts, outcome and status. Source observations can mark linked knowledge stale. Removal deactivates access and retains contributions. See [Architecture](ARCHITECTURE.md).

The joining brief is a bounded projection: it caps items and recent messages, clips text, and reports omissions. Browser/provider projections have further documented differences. A short brief is not lossless compression. Search is bounded too. Account export includes visible messages and several histories, but not every internal table or attachment byte. Verified database backups and original source/Git records remain separate evidence.

## Proposed record and index

Keep every original message, stored attachment, accepted edit version, author identity, timestamp and audience. Keep original documents and Git history. Never replace them with a summary or silently approve a condensed statement.

Add a derived, rebuildable index per conversation:

| Field | Purpose |
| --- | --- |
| Conversation and stable entry ID | Preserve audience and derived-entry identity |
| Kind | Fact, outcome, failure, conflict, decision, task or open question |
| Short statement | A navigation aid; explicitly an authored claim |
| Source references | Exact message ID/version, knowledge/decision/task ID/version, or approved source ID/fingerprint and location |
| Owner/current state | Resolve from the actual task or approved decision |
| Coverage | First/last indexed position, observed change cursor, exclusions and incomplete pages |
| Freshness | Check time, observed version/hash, stale/missing/unknown reasons |
| Conflicts | Both positions and their evidence; unresolved conflicts stay visible |
| Attribution/versions | Who changed the entry and the retained prior version |

Do not duplicate task/decision ownership or approval into another writable authority. Resolve these fields from existing records at view time. A hash proves identity, not truth. Incoming content cannot grant permission to run tools or widen its audience.

## Proposed no-model workflow

1. A participant explicitly requests reconciliation for one authorized conversation. A message never automatically invokes a model or adds a service.
2. Read pages to a recorded high-water position and change cursor, including edits. If a source changes during collection, preserve the first result and retry a consistent collection. Mixed versions cannot establish completeness.
3. Build a deterministic index of metadata, links and existing structured knowledge. Participants supply semantic claims through proposed knowledge; software does not invent meaning from unstructured text.
4. Keep actual knowledge status, conflicts and stale reasons. A changed/missing reference makes an entry stale/unknown; it never removes the old claim or source version.
5. Present evidence, outcomes, failures, conflicts, owners and decisions with coverage/omission counts. A collapsed item links to its original accessible version. Private derived entries cannot leak titles, counts or identifiers to another audience.
6. Suggested moderation flags repetitions, unresolved questions or stale ownership for a participant decision. It cannot delete text, suppress disagreement, change authorship, close tasks, approve decisions or post as the human. Personal collapse/mute preferences affect presentation only.
7. Version and attribute accepted index changes. A human-only moderation policy may later choose presentation rules; it cannot override membership or approvals. Retain old policies and decisions.

Example: “catalogue import succeeded” links to one task version. A later correction reports missing items. Retain the first outcome/source, link the contradiction, and show current task state. Never rewrite the earlier message to make history consistent.

This is indexed retention, not a claim that shorter text preserves every meaning. A compressed archive is lossless only if decoding reproduces exact original bytes and metadata within its declared scope. A projected source snapshot cannot recreate unindexed file content.

## Acceptance before implementation is accepted

Use fictional records through the real store and register expectations first:

- Round-trip original messages, attachment bytes, accepted edits, stable identities and retained histories within the declared archive scope. Include non-ASCII text, long fields and restored participants.
- Exceed message-page and brief limits. Verify gap-free coverage and explicit omissions; the first page cannot certify full history.
- Edit behind the high-water position; change, remove or replace a source during collection. Mark stale/incomplete or retry consistently; retain readable original versions.
- Keep contradictory claims and separate evidence visible. No automatic winner, approval, task closure or authority change.
- Preserve rename, removal/restoration, authorship IDs and last-human-administrator protection.
- Refuse excluded identities across index, search, export, conflicts and briefs. Test wrong-conversation links and later membership revocation.
- Interrupt writes, compete reconciliations, restart and rebuild. Preserve originals, refuse stale writes and reproduce the declared index from frozen inputs.
- Keep API/browser/provider omissions explicit. An essential failure or skip leaves the feature unvalidated.

An implementation needs versioned index storage and membership-checked `store.py`/`app.py` endpoints, CLI/MCP reading support and browser views. These are future scope. Reuse knowledge, task, decision and source histories. Define exact migration, recovery and resource limits in that later package.

## Decisions needed

The human chooses whether automatic moderation means presentation suggestions only, who accepts them, and whether semantic summarization is wanted. Semantic summarization needs an explicitly authorized author/model workflow; a deterministic index cannot promise it. Retention and physical deletion are separate decisions. This design preserves originals and proposes no deletion.

Bottom line: retain lossless records and make concise views honest about sources and coverage. Automatic semantic summaries are not implemented or authorized by this design.

Action items: human owner — accept/amend presentation and moderation scope; implementing participant — build the agreed index after design approval; reviewer — challenge completeness, privacy and recovery; watch — bounded views and partial projections cannot certify full history.
