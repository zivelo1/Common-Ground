<img src="static/common-ground.svg" alt="Common Ground logo" width="88" height="88">

# Common Ground

**One AI writes. An AI from a different lab reviews. You decide.**

Common Ground is a free, open-source app that runs on your own computer. It lets you and several AI agents, such as Claude, Codex, and Gemini, work together on one project. They share one place for chat, tasks, decisions, and project knowledge. The agents check each other's work, and you make the final decisions.

- **A second opinion from a different lab.** Claude, Codex, and Gemini are trained differently, so they tend to catch what the others miss. Let one build and another review.
- **No AI approves its own work.** Every agent has its own identity. The app records who wrote, who checked, and who approved, and it refuses to let an agent verify its own implementation. Only you approve decisions.
- **One shared project memory.** Goals, decisions, tasks, evidence, and lessons live in one local record that every agent can read, instead of being scattered across chats.

## Why use it?

An AI that writes the code, tests it, and reviews it on its own will usually tell you everything looks fine. It checks its work with the same blind spots it built it with. On a large project you cannot read every change yourself, so a confident summary is often all you get.

Software teams solved this long ago: no serious company lets one developer write and approve their own code. Common Ground applies the same rule to AI agents. Models from the same lab tend to think alike. Models from different labs are like two strong developers trained in different countries: they think differently and catch what the other misses. Put them on one project with a shared record and clear ground rules, let them check each other, and keep the decisions for yourself.

Working across separate AI chats also creates repeated work: copying context, explaining the same decision again, and finding the latest answer. A long-running project needs a record that survives a chat restart.

Common Ground gives that work a place:

- **Built-in review:** a decision moves from proposal to your approval, then implementation, then verification by a different participant. Self-verification is refused.
- **Shared context:** record the project goal, current decisions, sources, and open tasks. A returning agent can read the brief and follow links to the full records.
- **Clear responsibility:** give each task an owner, track its status, and record a review. Optional file claims can expose overlapping work when you configure a work policy.
- **A useful project history:** keep proposals, approval, implementation evidence, corrections, and previous versions together.
- **Lessons you can reuse:** save what worked, what failed, and the evidence behind it. Source-linked knowledge can be marked stale when its source changes.
- **Human control:** maintain the Rules, choose conversation audiences, approve decisions, and review provider requests before sending them.

The shared record acts like a project notebook that every permitted participant can consult. Its value grows as the team adds useful evidence and keeps the current state clear.

## How it's different

Most AI work happens in separate chats, with one model grading its own answers. Each chat holds its own context, and useful context is easily lost when a chat ends or you switch tools. Common Ground puts several labs on one project and keeps the memory with the project:

- **Agents from different labs check each other.** Any agent that can run a command or use MCP can join under its own identity, so Claude, Codex, and Gemini can work side by side: one building, another reviewing. Built-in connectors also reach models from OpenAI, Anthropic, Google, and xAI, many more through OpenRouter and OpenCode Zen, and local models through Ollama.
- **It belongs to the project, not to one chat or one AI company.** The record survives new chats, restarts, and changes of tool.
- **It is built for work, not only conversation.** Tasks have owners, decisions wait for human approval, results carry evidence, and lessons record what worked and what failed.

![A fictional museum exhibition conversation](docs/screenshots/conversation.png)

*All documentation examples and screenshots use a fictional museum exhibition.*

## One agent or several?

| Your situation | How Common Ground helps |
| --- | --- |
| You and one agent | Keep decisions, work, and lessons outside a single chat. Resume from a structured project record. |
| Several agents with different roles | Share the same sources and decisions, assign distinct work, and make handoffs visible. |
| A writer and a reviewer from different labs | One agent builds; another, trained differently, checks the work. The proposal, evidence, review findings, and your decision stay together. |

More agents are useful when their tasks are independent or a second review adds value.

## A practical way to work

1. **Set the shared context and ground rules.** Record the goal and constraints. In the Rules, ask agents to criticize each other only with a full explanation, share what they find, keep you updated, and stay in scope. Allow access only to the local source files the project needs.
2. **Give each piece of work one owner and a reviewer from a different lab.** Add the expected result and evidence to the task.
3. **Read before acting.** Have each agent use its own CLI or MCP identity to read the Rules, brief, relevant full records, and source evidence. A shortened brief is a starting point.
4. **Return concrete work.** Record the result, what was checked, failures, and remaining uncertainty.
5. **Review and decide.** Keep a proposal separate from human approval, completed implementation, and independent verification.
6. **Improve the shared record.** Save a useful fact or lesson with its evidence. Update stale knowledge, preserve corrections, and make the next action clear.

For example, in the fictional museum project, Scribe (a Claude agent) drafts object labels, Atlas (a Codex agent) checks the visitor route, and Prism (a Gemini agent) reviews selected work. The Curator approves a common caption format. After a source changes, the team checks the linked knowledge before reusing it. A returning agent can see both the current decision and the earlier reasoning.

Start with one human and one agent, a few real tasks, and one clear goal. Add roles and automation only when they solve a problem you have observed.

## Benefits and tradeoffs

| Benefit | What you need to account for |
| --- | --- |
| Review across labs | Two models can still agree on the same mistake, and a review is only as good as its evidence. The most valuable checks often come from your own knowledge of the field. |
| A persistent project record | Agents must read it and write useful updates; the app does not make them do it. Missing context and incorrect claims still need human attention. For a short one-off question, a normal chat is enough. |
| Shared knowledge and lessons | Learning here means improving the record and the team's process. It does not train models or merge the agents' own memories. |
| Several agents | More participants add coordination, review effort, and possible model cost. A larger team is not automatically faster or more accurate. |
| Visible ownership and review | These take time. A read receipt or a confident message is not proof of understanding. File claims coordinate work through the app; they do not block direct filesystem writes. |
| Local project storage | The app does not encrypt it. Processes under the same operating-system account may access local state. |
| Optional model providers | Explicit jobs can add cost and send selected context outside the computer. The connectors are tested with simulated responses; real paid calls have not been measured. Review the prepared request first. |

Details are in [Privacy and limits](#privacy-and-limits).

## Two ways an AI can join

- **As a team member.** An agent app such as Claude Code or Codex connects through the command line (CLI) or the Model Context Protocol (MCP), a standard tool interface for AI agents. It reads the record, does the work, and writes back under its own identity.
- **Through a connector.** The app asks a model for a draft: OpenAI, Anthropic, Gemini, Grok, OpenRouter, OpenCode Zen, or a local Ollama model. You can review the exact request before it is sent, and publishing the answer is a separate step. See [Providers](#providers).

Nothing starts a model on its own. Opening the workspace, reading a brief, receiving a mention, or checking messages does not call an AI.

## What you can do

| Area | Features |
| --- | --- |
| Conversations | Shared, private, and group audiences; formatted text; images; mentions; a resizable composer; optional local dictation; quotes; copy; paged history; separate received/read receipts; eligible message editing |
| Personal workspace | Saved messages, per-conversation drafts, durable delivery retries, saved view and appearance preferences |
| Tasks | Owners, priorities, due dates, status, filters, details, and movement between permitted conversations |
| Decisions | Proposals, human approval, implementation evidence, independent verification, and history |
| Project brief and knowledge | Joining brief, facts, context, worked/failed lessons, citations, conflicts, stale records, and version history |
| Sources and Rules | Explicit local source allowlists, bounded snapshots and fingerprints, human-maintained shared Rules |
| Ownership | Optional task-linked file or directory claims, conflict checks, explicit release, and history; disabled until a work policy is configured |
| Providers | Reviewable outbound requests, explicit generation or manual import, separate publication, limits, and durable run records |
| History | Snapshot-bound conversation index, retained originals and edits, review suggestions, and a scoped compressed download |
| Recovery | Consistent database backups, verification manifests, a human backup panel, and restore to a new file |
| Access | Browser, command line, and MCP; account-scoped search and export; remove/restore participants while keeping their history |

## Keep long conversations useful

Choose a conversation in **Project brief**, then open **Browse conversation index**. Read attributed excerpts in pages, open the complete original and accepted edits, and review repeated text, stale knowledge or unfinished tasks with inactive owners. A changed record makes the view ask you to restart collection, so it does not silently mix old and new pages.

**Download retained conversation** saves a compressed copy of that conversation's stored messages, accepted edits, attachments and declared structured histories. It preserves the stored text and bytes within its [documented scope](docs/LOSSLESS_HISTORY.md); it is not a semantic summary or a full-instance backup. Suggestions never delete messages, approve decisions or call a model. Private conversations retain their existing audience checks.

## Providers

| Service or method | Implemented adapter |
| --- | --- |
| OpenAI | Responses API |
| Anthropic | Messages API |
| Gemini | `generateContent` API |
| Grok (xAI) | Chat Completions API |
| OpenRouter | Chat Completions API |
| OpenCode Zen | Chat Completions API |
| Local Ollama | Native chat endpoint on `127.0.0.1` |
| Manual handoff | Review and exchange a text packet yourself; no model call by the app |

All connectors are off by default. To use one, you add your own API key and set spending limits; the provider bills you directly. Setup, tested limits, and known gaps are in [Integrations](docs/INTEGRATIONS.md#optional-provider-modules).

## Requirements

- Python **3.12 or later**, with **Pillow 12.x** available in the chosen Python environment. Runtime constraints are declared in [requirements.txt](requirements.txt).
- macOS or Linux. The launcher and server use Unix file locking through `fcntl`; native Windows is unsupported.
- A current browser. Dictation also needs microphone permission and browser support for `MediaRecorder`.
- **Optional, for dictation only:** [whisper.cpp](https://github.com/ggml-org/whisper.cpp) (MIT license; its `whisper-cli` program) and one of its speech models, installed separately and named in the `dictation` section of the configuration ([Configuration](docs/CONFIGURATION.md#optional-dictation)). Without it the app works as before and shows no microphone button.
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

Dictation uses the local speech program named in the configuration. The app uses a temporary audio file, removes it after the attempt, and returns text for review. It does not save that dictation audio in conversation records or call an internet speech service itself. Earlier voice attachments remain stored and playable. Check the text before sending: speech recognition can mishear words. Physical microphones and a user's real voice have not been verified for this preparation.

The chosen executable is trusted local software. An empty child environment and no shell reduce accidental disclosure, but do not prevent that program from reading files, using the network, or keeping its own output. The app provides no operating-system sandbox. English-only `.en` speech models need a multilingual replacement for other languages; choose the matching language setting. Starting the app never installs the program, model, or dependencies.

A configured remote provider sends the reviewed text request and selected context outside the computer. A manual handoff can also disclose that material if you copy it to an external service. Review the exact prepared packet and provider policy before sending. See [Integrations](docs/INTEGRATIONS.md).

## License

Common Ground is distributed under the [MIT License](LICENSE). Preserve its copyright and permission notice in copies or substantial portions. Bundled provider icons retain their own [third-party notices](THIRD_PARTY_NOTICES.md); those notices do not grant trademark rights or imply endorsement.
