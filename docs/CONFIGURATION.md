# Configuration

Use a private configuration for each project and a separate state directory. The public `config.example.json` is a starting template; `config.json` belongs to the local instance and must not be published.

## Minimal museum project

```json
{
  "title": "Common Ground",
  "project_label": "Museum exhibition",
  "state_dir": "state",
  "port": 0,
  "browser_session_days": 30,
  "participants": [
    {"id": "curator", "name": "Curator", "kind": "human"},
    {"id": "atlas", "name": "Atlas", "kind": "agent"},
    {"id": "scribe", "name": "Scribe", "kind": "agent"},
    {"id": "prism", "name": "Prism", "kind": "agent"}
  ],
  "source_roots": [],
  "source_files": []
}
```

Atlas, Scribe, and Prism are fictional museum agents in this example. A participant ID identifies an account, not an automatic connection to a model.

## Core fields

| Field | Meaning |
| --- | --- |
| `title` | Workspace title |
| `project_label` | Project label in navigation |
| `state_dir` | Private instance directory; relative paths resolve beside the configuration |
| `port` | Integer from 0 to 65535; 0 selects a port at startup |
| `browser_session_days` | Browser-session lifetime, 1–365 whole days; default 30 |
| `participants` | Distinct registered identities with display name and `human` or `agent` kind |
| `source_roots` | Explicit approved roots for source selection and optional work policy |
| `source_files` | Individual indexed files; an empty list indexes nothing |
| `knowledge_check` | Optional per-conversation daily-check time and time zone |
| `dictation` | Optional local speech-to-text program for the microphone button |
| `work_permissions` | Optional explicit policy for task-linked file claims |

Participant IDs begin with a letter or digit and use letters, digits, underscores, or hyphens, up to 64 characters. IDs must be unique. At least one human is required by the store; the launcher requires exactly one human owner. Configure each agent client with its own ID.

Names are display labels. A human can rename participants in the application without changing IDs or credentials. Do not change an established ID to perform a display-name edit. The application can add an agent account through the human interface; a provider module still needs its own configuration. Use **Remove** and **Restore** for access changes. Removal keeps the identity, old records and change history; restart does not silently restore it. Removal deletes browser sessions. Restore keeps credentials and allowed access, but the browser needs a fresh login. Both the provider caller and its registered agent must be active. These controls cannot recall context already copied or sent outside the app.

An optional `logo` must name a bundled, registered logo. Use an appropriate provider identifier rather than treating a logo as evidence of a live connection or endorsement.

## State and restart

State includes the SQLite database, credential files, runtime metadata, locks, logs, provider run records, and backups. These are private, even when a project contains only ordinary exhibition material. Do not place state under a source indexing root or in a public upload.

Stop the instance before changing its established identity, path, or provider configuration. Reopen with the same configuration to reuse the database. Saving personal state is account-scoped; changing the port does not erase acknowledged drafts or queued sends. It may change browser authentication and browser-only settings.

A fixed port can make a local bookmark more convenient, but it can be occupied. The server still binds to `127.0.0.1`. This is not a configuration for access from another computer.

## Explicit source indexing

Select roots, then individual files. A relative root path resolves beside the configuration. Each file path is relative to its named root; absolute paths and traversal segments are refused.

```json
{
  "source_roots": [
    {
      "id": "museum",
      "path": "museum-project",
      "git": false,
      "exclude": ["visitor-records", "private-notes"]
    }
  ],
  "source_files": [
    {
      "id": "exhibition-plan",
      "root": "museum",
      "path": "docs/exhibition-plan.md",
      "title": "Exhibition plan",
      "channel_id": "workspace",
      "mode": "text",
      "max_bytes": 65536
    }
  ]
}
```

Merge these fields into the project configuration. Create the intended fictional source separately; the application does not manufacture a missing document. Assign a private source only to a conversation whose membership is permitted to see it. Use actual conversation IDs returned by the app for private or group audiences.

Root exclusions add restrictions. Built-in privacy checks also reject protected locations and sensitive file types. Do not rely on those checks to discover every confidential file; the explicit file selection is the main boundary. Symbolic links, linked path aliases, and unsupported file layouts can be refused.

| Mode | Retained material |
| --- | --- |
| `text` | Bounded decoded text and source metadata |
| `metadata` | Source identity and fingerprints without its body |
| `json_projection` | Explicit selected fields with bounded depth, item counts, and text |

The default source byte limit is 512 KiB. `max_bytes` must be positive and no greater than 128 MiB. At most 100 source files can be configured. Limits and projection warnings can leave content unindexed; always check the original when a conclusion depends on omitted material.

If `git` is true, source indexing can record local Git revision/blob information. This does not clone, fetch, push, or publish the repository.

### JSON selection

A projection needs explicit selectors. Each selector names an object path and fields; it is not a recursive query or permission to include the entire document.

```json
{
  "id": "display-status",
  "root": "museum",
  "path": "data/display-status.json",
  "title": "Display status",
  "channel_id": "workspace",
  "mode": "json_projection",
  "max_bytes": 65536,
  "selectors": [
    {"path": ["display"], "fields": ["title", "status", "checked_at"]}
  ]
}
```

For a document containing `{"display": {"title": "Ceramics", "status": "reviewed", "checked_at": "2026-10-12"}}`, this selects those three fields. Broad instruction, body, log, and credential field names are deliberately refused. Valid field names can still contain private values; review the selected data before allowing it into a shared conversation or provider packet.

Humans can refresh sources explicitly. Reads also observe freshness and can mark linked knowledge stale. Observing a source is not approval of its claims or execution of its text.

## Daily knowledge-check setting

```json
{
  "knowledge_check": {
    "time": "17:00",
    "timezone": "Europe/London"
  }
}
```

The setting requires exactly `time` and `timezone`. Time uses a 24-hour `HH:MM` value; the time zone is an IANA name available to Python. Omit the field or use `null` to leave checks unconfigured.

Checks are recorded per conversation and check day. The day starts at the configured time. The setting does not start an agent or create an automatic task. It is separate from the browser's detected local time zone for displaying conversation timestamps.

## Optional dictation

```json
{
  "dictation": {
    "command": "/usr/local/bin/whisper-cli",
    "model": "/home/curator/models/ggml-small.en.bin",
    "language": "en",
    "threads": 4,
    "timeout_seconds": 60,
    "max_seconds": 120
  }
}
```

Dictation turns a short spoken memo into text in the human's message box; the human checks it and sends it as an ordinary message. It needs [whisper.cpp](https://github.com/ggml-org/whisper.cpp)'s `whisper-cli` program and one of its speech models, installed separately. The setting requires all six fields:

| Field | Meaning |
| --- | --- |
| `command` | Absolute path to the executable `whisper-cli` |
| `model` | Absolute path to a whisper.cpp model file; an `.en` model understands English only |
| `language` | Two- or three-letter language code, or `auto` |
| `threads` | Processor threads for one transcription, 1–64 |
| `timeout_seconds` | Longest wait for one transcription, 1–600 |
| `max_seconds` | Longest memo, 1–240; recording stops by itself at this length |

The paths above are fictional examples; use your chosen installed executable and model. The app validates their presence and does not install them. An `.en` model is English only. Use a multilingual model and a suitable `language` value for other languages; accuracy still needs your own check.

The server runs the program directly, without a shell and with an empty environment, one transcription at a time. It gives the program the workspace title, project label and active participants' names as spelling hints. The app removes its temporary recording after the attempt and does not store dictation audio in conversation records. A silent recording is refused before the program runs, because a speech model may invent a word for silence. Omit the field or use `null` to leave dictation off; the microphone button then does not appear.

The executable is trusted software, not sandboxed software. An empty environment does not stop it from reading files, using the network or writing its own records. Choose the executable and model deliberately. Physical microphone and real-voice accuracy have not been verified for this preparation.

## Optional file work policy

Omit `work_permissions` until the human has approved the roots, audiences, and paths. Merely adding a source root or source file does not enable ownership claims.

For the fictional `museum` root above:

```json
{
  "work_permissions": {
    "schema_version": 1,
    "max_active_claims": 12,
    "rules": [
      {
        "root_id": "museum",
        "channel_id": "workspace",
        "path": "exhibits",
        "kind": "tree"
      }
    ]
  }
}
```

This permits claims for the configured exhibition tree in the shared conversation, subject to path validation and conflict checks. A `file` rule names a single file; a `tree` rule can cover approved descendants. Each rule requires exactly `root_id`, `channel_id`, `path`, and `kind`. Paths are explicit relative paths using forward slashes. Rules cannot cover protected or excluded paths. The active-claim cap is a positive integer.

Only the current task owner can create its claim. The request includes the current task version and a stable client ID for retries. Overlapping active claims are checked across aliases and case-normalized paths. A claim can be rejected without exposing details from a private audience.

An owner or an accessible human releases a claim with its current version and a reason. Claims do not expire or disappear when a task finishes. If policy is removed, new claims are disabled while retained history and permitted releases remain available.

A claim coordinates participants; it does not prevent direct filesystem writes. The code or editor doing the work needs its own enforcement. Read-only indexing permission and write permission are different decisions.

## Providers and backup schedule

Provider setup is in a separate private file derived from `providers.example.json`. Its sample modules are disabled and unresolved. Six remote adapters (OpenAI, Anthropic, Gemini, Grok/xAI, OpenRouter, OpenCode Zen), local Ollama, and manual handoff are implemented. Choose the provider, compatible model, active registered agent and permitted conversation deliberately.

Save the provider file beside `config.json` as `providers.local.json`. Human launching also needs `adapter-human-policy.local.json`. The ordinary launcher passes these private files to the server when present; without a provider file, providers stay off. A grant file without its provider file is refused. Neither file belongs in the public package.

Remote keys are environment values referenced by `credential_env`, never values in the configuration. Remote activation also requires dated pricing evidence and explicit job/budget limits. The app does not contact a provider to discover missing settings, does not install software, and does not launch models because messages or mentions arrive. See [Integrations](INTEGRATIONS.md) for exact endpoints, limits and the distinction between supported code and measured provider calls.

The database backup interval is maintained through the human **Backups** view, not a `config.json` field. It defaults to three days and accepts 1–365 whole days. The server's normal entry point runs the schedule while open. Verification and restoration are described in the [User guide](USER_GUIDE.md#11-back-up-and-restore).
