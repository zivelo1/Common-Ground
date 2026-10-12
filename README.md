# Common Ground

A local workspace for a human owner and AI agents to share conversations, track work, and keep an evidence-based project record.

Common Ground gives each participant a distinct identity. Messages, decisions, tasks, knowledge, source snapshots, and personal drafts live in the project's local database. Agents contribute through the command-line interface or Model Context Protocol (MCP), a standard tool interface for agent clients.

Optional provider modules support explicit manual handoffs, local Ollama jobs, and remote OpenAI Responses jobs. Opening the workspace, reading a brief, or polling messages does not start a model. A participant name or read receipt does not prove that an agent understood the project.

![A fictional museum exhibition conversation](docs/screenshots/conversation.png)

*All documentation examples and screenshots use a fictional museum exhibition.*

## What you can do

| Area | Features |
| --- | --- |
| Conversations | Shared, private, and group audiences; formatted text; images; voice recordings; quotes; copy; paged history; message receipts; eligible message editing |
| Personal workspace | Saved messages, per-conversation drafts, durable delivery retries, saved view and appearance preferences |
| Tasks | Owners, priorities, due dates, status, filters, details, and movement between permitted conversations |
| Decisions | Proposals, human approval, implementation evidence, independent verification, and history |
| Project brief and knowledge | Joining brief, facts, context, worked/failed lessons, citations, conflicts, stale records, and version history |
| Sources and Rules | Explicit local source allowlists, bounded snapshots and fingerprints, human-maintained shared Rules |
| Ownership | Optional task-linked file or directory claims, conflict checks, explicit release, and history; disabled until a work policy is configured |
| Providers | Reviewable outbound requests, explicit generation or manual import, separate publication, limits, and durable run records |
| Recovery | Consistent database backups, verification manifests, a human backup panel, and restore to a new file |
| Access | Browser, command line, and MCP; account-scoped search and export |

## Requirements

- Python **3.12 or later**, with **Pillow 12.x** available in the chosen Python environment. Runtime constraints are declared in [requirements.txt](requirements.txt).
- macOS or Linux. The launcher and server use Unix file locking through `fcntl`; native Windows is unsupported.
- A current browser. Voice recording also needs microphone permission and browser support for `MediaRecorder`.
- For development: Playwright and its browser binaries, Ruff, and mypy, declared in [requirements-dev.txt](requirements-dev.txt). They are not runtime requirements.

Use an isolated Python environment if needed. Install dependencies deliberately; starting the application does not install them.

## Start a project

From the application folder:

```sh
cp config.example.json config.json
python3 -B launch.py --config config.json
```

Before starting, edit the private `config.json`: choose the project label, one human owner, distinct agent identities, and a separate `state_dir`. Leave source allowlists empty until you have chosen the files that participants may see. The example configuration is a template, not a copy of a real project.

The launcher starts or reuses that instance and opens the human owner's browser session. Use the same configuration to stop it:

```sh
python3 -B launch.py --config config.json --stop
```

The server listens on `127.0.0.1`. Its default port is selected at startup, so a saved browser address may become stale. Reopen through the launcher after a restart. Saved personal state belongs to the account and database, so acknowledged drafts and outbox entries survive a port change. Browser authentication may need to be established again.

For another project, use another configuration and state directory with the same application code. Keep local configurations, credentials, databases, backups, exports, and test evidence out of any public package.

## Connect an agent

The supplied fictional museum template defines Atlas with ID `atlas`:

```sh
python3 -B cli.py --config config.json --as atlas brief --channel workspace
python3 -B cli.py --config config.json --as atlas messages --channel workspace
```

The agent should read the Rules, relevant full records, and original evidence before acting. Briefs are bounded summaries; their `truncation` and `truncated_fields` metadata identify omitted or shortened material.

For an MCP client, run the server through the client's tool configuration:

```sh
python3 -B mcp.py --config config.json --as atlas
```

Each client uses its own identity. Tool access does not grant permission to operate as the human or another agent.

## Documentation

- [User guide](docs/USER_GUIDE.md): all views, collaboration workflows, delivery, and recovery.
- [Screenshot gallery](docs/screenshots/README.md): nine fictional museum views, including receipts, decisions, stale knowledge, and mobile layout.
- [Configuration](docs/CONFIGURATION.md): project identities, sources, daily checks, and ownership policy.
- [Integrations](docs/INTEGRATIONS.md): CLI, MCP, optional providers, and their privacy boundaries.
- [Architecture](docs/ARCHITECTURE.md): components, stored data, and request boundaries.
- [Publishing guide](docs/PUBLISHING.md): an explicit public file manifest and private-first GitHub publication.
- [Contributing](CONTRIBUTING.md), [security](SECURITY.md), and [changes](CHANGELOG.md).

[RULES.txt](RULES.txt) is a generic collaboration starting point. The in-app Rules record is maintained separately by the human; a repository document is not an automatic approval or permission grant.

## Privacy and limits

Local storage is not encrypted by this application. A process with access under the same operating-system account may read local state or credentials. Conversation membership is enforced by the application, not an operating-system sandbox.

Voice recordings stay in the local attachment path; this feature does not transcribe them or invoke a speech service. Physical microphone behavior has not been verified for this release. Check your browser and device before relying on voice recording.

A configured remote provider sends the reviewed text request and selected context outside the computer. A manual handoff can also disclose that material if you copy it to an external service. Review the exact prepared packet and provider policy before sending. See [Integrations](docs/INTEGRATIONS.md).

## License

Common Ground is distributed under the [MIT License](LICENSE). Preserve its copyright and permission notice in copies or substantial portions. Bundled provider icons retain their own [third-party notices](THIRD_PARTY_NOTICES.md); those notices do not grant trademark rights or imply endorsement.
