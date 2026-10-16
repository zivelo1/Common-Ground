# Conversation index and retained history

The conversation index is a navigation view built from the retained database. It does not replace messages, invent semantic summaries, delete repetition or approve decisions. Reading an index, full record or archive records retrieval but does not acknowledge reading.

## Browse one conversation

In **Project brief**, select one conversation, then choose **Browse conversation index**. The index shows message IDs, author names, versions, dates and shortened excerpts. **Read original and edits** opens the complete retained original, current message and accepted edit versions. Excerpts are navigation aids, not lossless summaries.

Pages contain at most 200 messages. The view shows how many messages have been collected out of the conversation total. Every page is bound to the same fingerprint of that conversation's messages, edits, current members, message authors, tasks, decisions, knowledge and sources. A change behind the cursor also invalidates collection. Old rows remain visible on a conflict; use **Restart index** to collect a consistent view. A fingerprint proves identity, not truth.

The API separates `page_reaches_end` from `complete`: the final page can reach the end while omitting earlier messages. `omitted_before`, `remaining`, `returned`, `total` and `high_water` describe the response. Only a response covering the entire conversation has `complete: true`. A client combining pages must start at zero, retain one snapshot, and check its collected count against the total. Global message IDs can have gaps between conversations.

Each request checks current membership. A private index includes only that conversation; public source records are not silently mixed into it. Revoked membership refuses subsequent reads. Sources are existing indexed observations, not freshly read files.

## Review suggestions

The index identifies exact repeated message text, stale/disputed/conflicting knowledge and unfinished tasks with inactive owners. It preserves the original records and current decision statuses. A suggestion cannot close a task, approve a decision, hide disagreement, change authorship or send a message as the human. It does not infer whether different sentences mean the same thing.

Example: two identical catalogue instructions produce one repetition suggestion linking both message IDs. Both full originals remain readable, and any proposed decision still needs the human's approval.

## Download the retained conversation

**Download retained conversation** exports a deterministic gzip-compressed JSON envelope. It contains one transaction's complete records within the following declared scope:

- Current messages, their retained original bodies and accepted edits.
- Stored image attachment bytes, encoded as base64, and their recorded metadata.
- Current tasks, decisions, knowledge and configured source observations in the selected conversation.
- Retained decision, knowledge and source histories belonging to those records.
- Conversation identity and current identity metadata for message authors.

The envelope records its format version, per-table counts, scope, exclusions and a SHA-256 fingerprint of the canonical payload. Decoding verifies the fingerprint and size limit; it does not authenticate authors or prove the content true. Unicode text and attachment bytes are retained exactly as stored. Repeated exports of unchanged data have identical compressed bytes.

The archive excludes credentials, browser sessions, personal drafts/preferences, other conversations, instance-wide participant rename/removal histories, Rules, read receipts and file claims. It also excludes original external files and Git objects; source snapshots retain only the observations already stored. This scoped download is not a full instance backup and has no import/restore endpoint. Use the existing verified database backup workflow for full-instance recovery and preserve original documents/Git separately.

An index binds at most 128 MiB of canonical record data. An archive has a 128 MiB uncompressed envelope limit; attachment base64 counts toward it. Oversize requests fail explicitly rather than returning an apparently complete partial archive. Archive collection holds a database transaction while encoding, so large exports can delay writers; perform them deliberately.

## CLI and MCP

```sh
python3 -B cli.py --config config.json --as atlas conversation-index --channel workspace
python3 -B cli.py --config config.json --as atlas conversation-index --channel workspace --after 200 --snapshot RETURNED_FINGERPRINT
python3 -B cli.py --config config.json --as atlas conversation-record --channel workspace --message-id 42
python3 -B cli.py --config config.json --as atlas conversation-archive --channel workspace --output conversation-history.json.gz
```

Use the actual returned `next_after` and fingerprint; the numbers above illustrate arguments only. The archive command verifies decoding before creating a private file with mode 0600, refuses an existing destination and reports errors. Keep private downloads private. MCP provides `collab_conversation_index` and `collab_conversation_record` under the configured participant identity; archive downloading is available through the browser or CLI.

## Verification and limitations

The regression suite uses fictional records through the real store, local HTTP, CLI, MCP and browser. It checks pagination beyond 200 messages, edits behind a cursor, wrong-channel links, membership revocation, private-channel separation, unchanged approval states, Unicode, retained edit versions, exact attachment bytes, archive corruption and decompression limits. Browser checks exercise restart after a changed snapshot, full-record reading and download.

No schema migration, model call, background service or automatic semantic moderation is added. The database remains authoritative and the index is rebuilt on demand. Source refresh, provider calls and external host isolation are separate operations. Full package verification and independent review must finish before release acceptance; these feature descriptions are not a live-installation receipt.
