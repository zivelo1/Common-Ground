# Integrations

The browser, CLI, and MCP all act through a registered identity and the same account-scoped store. Optional provider modules add explicit text jobs; ordinary messages and reads do not wake models.

## Command-line interface

Run commands from the application folder with the intended configuration and your own participant ID. Output is JSON; errors go to standard error and return a failing exit code.

For the fictional catalogue agent:

```sh
python3 -B cli.py --config config.json --as atlas status
python3 -B cli.py --config config.json --as atlas channels
python3 -B cli.py --config config.json --as atlas brief --channel workspace
python3 -B cli.py --config config.json --as atlas messages --channel workspace
```

The client reads private runtime metadata and that identity's credential locally. It targets only the validated loopback API and refuses redirects. Do not put credentials in command arguments, public files, or copied output.

### Command inventory

| Commands | Purpose |
| --- | --- |
| `status`, `channels`, `decisions`, `tasks` | Current visible workspace records |
| `brief`, `knowledge` | Bounded joining summary or full visible knowledge records; optional conversation scope |
| `messages` | A page after the supplied message cursor |
| `send` | Text or standard-input message with optional stable client ID |
| `wait` | Bounded message polling, optionally scoped by conversation |
| `remember`, `update-knowledge` | Standard-input JSON contribution or versioned update |
| `knowledge-history`, `source-history` | Attributed retained versions and source snapshots |
| `refresh-sources` | Explicit human-only refresh |
| `knowledge-check`, `knowledge-check-status` | Record or inspect a configured daily check |
| `export` | The account's visible records, without credentials |
| `request` | An explicit local GET or POST API request |

Run `python3 -B cli.py --help` and a command's `--help` for its exact arguments.

### Send and retry

```sh
python3 -B cli.py --config config.json --as atlas send \
  --channel workspace --client-id museum-label-review \
  --body 'The fictional ceramics label has source citations ready for review.'
```

Generate a distinct client ID for each new logical message. Reuse it, with exactly the same body and audience, if storage acknowledgement is lost. Reusing it for changed content is refused. For long text, use `--from-stdin`; this also avoids putting the body in process arguments.

### Pagination and acknowledgements

`messages --after` returns one page of at most 200 messages and continuation metadata. Follow the returned cursor until the relevant range is complete. One call is not a full conversation archive. Browser history also loads in pages.

`wait --timeout` accepts 0–60 seconds. It checks for messages; it does not start a model, send a reply, or mark anything read. Preserve returned cursors rather than inferring completeness from the newest timestamp.

MCP provides `collab_read` for explicit read acknowledgements. A CLI client can use `request` with POST `/api/read` and the actual returned `channel_id` and `message_id`. A read marker is an application acknowledgement, not evidence of understanding.

### Full records after a brief

Inspect `truncation` and `truncated_fields` in each brief. Use `status`, `knowledge`, `decisions`, `tasks`, relevant history commands, paged messages, or `export` for material omitted from the summary. Read indexed source snapshots and original documents when source selection was bounded. The JSON brief returned by CLI/MCP includes shared Rules whole and daily-check data for a selected conversation. The browser joining view requires a separate visit to Rules; its copy/export summary omits Rules and daily-check data as well as retaining the usual clipping limits.

## Model Context Protocol

MCP is a standard way for agent clients to invoke tools. Configure the client to start:

```sh
python3 -B mcp.py --config config.json --as atlas
```

The tool process uses JSON-RPC on standard input/output. Use your client's executable and working-directory settings so the configuration resolves to the intended project. Initialization only connects the tool protocol; it does not prove that the agent read a brief or took part in the conversation.

Start by calling `collab_brief`, then read the relevant full records and source evidence. Use channel IDs returned by the application rather than guessed private conversation IDs.

| Tool group | Tools |
| --- | --- |
| Context and records | `collab_brief`, `collab_state`, `collab_knowledge`, `collab_search` |
| Messages | `collab_messages`, `collab_send`, `collab_read` |
| Knowledge | `collab_remember`, `collab_update_knowledge`, `collab_knowledge_history`, `collab_source_history`, `collab_refresh_sources` |
| Decisions | `collab_propose_decision`, `collab_decision_status` |
| Tasks | `collab_task`, `collab_task_status` |
| File ownership | `collab_work_permissions`, `collab_file_claims`, `collab_claim_file`, `collab_release_file_claim`, `collab_check_file_claim`, `collab_file_claim_history` |

Schemas constrain available fields. The browser and generic CLI request surface expose some features beyond these MCP tools: rich task details/priorities/movement, daily checks, attachments, personal drafts, backups, provider jobs, and message editing. Do not assume MCP offers a tool for every browser control.

An agent cannot cross the human approval boundary by requesting an approval status. The store rejects unauthorized actions even when a schema includes a status value. Independent verification must come from a participant other than the implementer. MCP itself does not call a model or charge for one; the surrounding agent client may use its own provider.

## Optional provider modules

Providers are off by default. `providers.example.json` contains disabled, unresolved entries. It is not a ready-to-run configuration and contains no real credential.

| Provider | Behavior and privacy boundary |
| --- | --- |
| `manual` | Prepares a packet and accepts a manually imported result; no model invocation by the app |
| `ollama` | Calls an explicitly configured native chat endpoint on `127.0.0.1`; activation requires operator evidence of local-only operation |
| `openai_responses` | Sends an explicit text request to the supported OpenAI Responses endpoint; requires an environment credential reference, dated pricing, and spend limits |

These adapters are text-only. Image and audio attachment bytes are not submitted by these reference adapters. Selected context and the typed prompt can still disclose sensitive information, including text references to attachments.

### 1. Configure a private provider file

Copy the example to a private local filename such as `providers.local.json`. Keep it out of publication. Choose the module deliberately:

- Its registered agent `participant_id` must exist.
- `workspace_config` must resolve to the same project configuration as the running instance.
- `allowed_channel_ids` explicitly lists conversations that may be exported to this module.
- `enabled` must be true only after the remaining requirements are satisfied.
- Enabled modules require explicit `limits`.
- A remote credential is named through `credential_env`; its value belongs in the environment or a secret-management workflow, never the file.

Instance limits are `max_concurrent`, `daily_budget_usd`, and `pricing_max_age_days`. Module limits require these fields:

| Limit | Meaning |
| --- | --- |
| `max_context_bytes` | Maximum prepared outbound request size |
| `max_response_bytes` | Maximum provider response size |
| `max_output_tokens` | Requested output-token bound |
| `timeout_seconds` | Explicit finite request deadline |
| `max_concurrent` | Module concurrency bound |
| `max_run_usd` | Per-run remote spend limit; nullable for non-remote modes |

A manual module has no endpoint or required model. Ollama requires an explicit model and an exact `http://127.0.0.1:PORT/api/chat` endpoint without credentials, query, or fragment. `local_only_verified` and `local_only_evidence` record the operator's assessment; loopback alone does not prove the model service has no remote behavior.

The remote module supports `https://api.openai.com/v1/responses`. Its `pricing` object requires `input_usd_per_million`, `output_usd_per_million`, `max_input_tokens`, `checked_at` in `YYYY-MM-DD` format, and an HTTPS `evidence` reference. Choose current provider/model values; this guide supplies no assumed rates. Pricing must fall within `pricing_max_age_days`, and the configured per-run and daily budgets must cover the pessimistic reservation. A rejected configuration does not contact the provider to fill in missing values.

Reservations and usage accounting govern this application's admitted jobs. They are not a statement of the provider account's total bill, and an interrupted or uncertain remote request can leave cost unknown.

### 2. Grant human launching only when intended

An agent can operate its own registered module. A human needs an explicit separate policy to operate that agent's module through the UI:

```json
{
  "schema_version": 1,
  "human_launchers": {
    "museum-manual": ["curator"]
  }
}
```

The module must be defined in the private provider file, and `curator` must be a registered human. Both caller and agent must belong to the selected conversation, which must also be on the module's export allowlist. A grant is an intentional delegation to use that agent's identity for these provider jobs.

### 3. Start the provider-aware server

Stop any ordinary instance using the same state directory first. Then start explicitly:

```sh
python3 -B app.py --config config.json \
  --providers providers.local.json \
  --adapter-human-policy adapter-human-policy.local.json
```

The human policy flag is optional if no human launching is granted. The ordinary `launch.py` startup does not pass these provider options. Once the provider-aware server is running, the launcher with the same configuration can reopen its human browser session or stop it. Keep the server's actual local address and private credentials out of public documentation or screenshots.

### 4. Prepare, run, review, publish

In **Provider workspace**:

1. Select the module and permitted audience.
2. Type an explicit museum request and choose **Prepare preview**.
3. Inspect the exact outbound packet, identity, endpoint, model, context, fingerprint, and maximum reservation.
4. Choose **Run once**, or open the manual handoff and import the reviewed text result.
5. Inspect the result and its status before choosing **Publish**.

Preparation is durable only after acknowledgement. Raw typing in the provider request box is not saved as a personal draft. Generation and publication are separate actions. Reading status, reloading, polling, or restarting never automatically generates or publishes a result.

The provider context uses a selected, bounded conversation brief, not the complete workspace. The current provider export contract does **not** include the board brief's shared `rules` or `knowledge_check` fields. Review those separately and explicitly include any necessary, authorized instruction in the prompt. Even a full board brief does not establish that the provider received it whole.

For remote use, the reviewed packet leaves the computer. The OpenAI adapter sets `store: false` in its request; that flag is not a blanket retention or confidentiality guarantee for the provider. Review the applicable provider account terms and data controls. A manual packet copied to another service crosses that service's privacy boundary as well.

### Durable status and uncertain outcomes

Recent run records preserve prepared requests, result status, and publication state. An interrupted remote operation may be failed, incomplete, refused, or unknown. Inspect its original run before deciding what to do. A new run can repeat generation and cost; do not treat uncertainty as permission for an automatic rerun.

Publication uses a stable identity to confirm or send the stored result. If publication acknowledgement is lost, retry the original run with its original prepared fingerprint. Do not create a new result solely to confirm delivery.

### Provider runner commands

`adapter_runner.py` also supports `prepare`, `run`, `status`, `import-result`, and `publish` with a private provider file and module ID:

```sh
python3 -B adapter_runner.py --providers providers.local.json \
  --module museum-manual prepare --channel workspace --prompt-from-stdin
```

The command reads the request from standard input. Later commands use the `run_id` and `prepared_sha256` returned by preparation; do not invent or replace them. Run a command's `--help` for the required flags. These commands use the module's registered identity and the same permission, limit, and journal boundaries.
