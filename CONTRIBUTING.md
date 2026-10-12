# Contributing

Contributions should improve the shared record without weakening identity, privacy, or recovery boundaries. Use a disposable fictional museum project for examples, fixtures, and screenshots.

The project uses the [MIT License](LICENSE). Ensure you have the right to contribute the material and preserve applicable [third-party notices](THIRD_PARTY_NOTICES.md).

## Set up deliberately

Use Python 3.12 or later on macOS or Linux. Pillow 12.x is the runtime dependency, declared in [requirements.txt](requirements.txt). Playwright with installed browser binaries, Ruff, and mypy are development tools declared in [requirements-dev.txt](requirements-dev.txt). Install these into an environment you control; the application must not install them automatically. Native Windows is unsupported because the current server/launcher use `fcntl`.

Keep local configuration, virtual environments, disposable databases, browser profiles, and test output outside the public file manifest. Use distinct participant IDs in a fresh private state directory. Do not copy a real instance's state or credentials into a fixture.

## Propose a focused change

Explain the user-visible problem, the expected behavior, affected files, and privacy or persistence risk. For a change to model export, identity, approval, file ownership, or recovery, describe the boundary being preserved.

A good report gives a concrete fictional example: “A catalogue agent retries a send after losing its acknowledgement; the original message must appear once.” It does not attach an actual conversation database or access token.

Keep one source of truth for each behavior. Avoid parallel replacement copies and unrelated cleanup. Document non-obvious ordering, transactions, and asynchronous lifecycle assumptions where they matter.

## Verify the behavior

Tests belong under `tests/`. Use synthetic text, generated media, and disposable application instances. Do not call paid or live providers in routine tests. Stub the transport where appropriate while exercising the real permission and record code. Never test against another identity's real credentials.

The Python suite is discoverable with:

```sh
python3 -B -m unittest discover -s tests -v
```

Browser tests require the development browser tools. Use the package's test support to confine temporary state and receipts. A missing browser dependency or skipped essential test is incomplete verification, not a clean pass.

Run Ruff and mypy against the changed supported modules using the project's established options. Report their exact scope and any untyped boundary; do not claim that a selected-file check covered the whole repository. Run a syntax check for changed JavaScript as appropriate.

Choose checks that can catch the actual defect. For example:

- Retry behavior: lost acknowledgement, stable operation identity, and changed-body refusal.
- Access: excluded participants' state, messages, search, export, and attachments.
- Versioned edits: competing saves and retained history.
- Voice: pending permission, stale callbacks, navigation cleanup, size limits, and retained player identity during refresh.
- Ownership: overlapping files/trees, disabled policy, private conflicts, task versions, and explicit release.
- Providers: disabled modules, identity grants, outbound packet scope, budget admission, unknown results, and publication retries.
- Backups: integrity/fingerprint checks, incomplete manifests, failure records, and refusal to overwrite a restore target.

A synthetic audio/browser pass does not prove physical microphone behavior. State that limit explicitly. Do not describe a simulated participant as a real agent who joined or understood the project.

## Review and handoff

Have an independent reviewer challenge the completed change. Resolve every finding or document why it is accepted. Present the final behavior and validation, including incomplete checks, without copying private evidence.

A contribution should include:

1. The problem and resulting behavior.
2. Focused implementation and any user-facing documentation update.
3. Meaningful verification bound to the candidate source.
4. Risks, compatibility, and recovery steps when state changes.
5. An updated public-file manifest if new publishable files are added.

Stage explicit reviewed files. Inspect the staged diff before committing or submitting. Follow the [publishing guide](docs/PUBLISHING.md) for history and privacy review; ignored files can already be tracked.

## Report security problems privately

Follow [SECURITY.md](SECURITY.md). Do not open a public issue containing credentials, private conversations, exports, or a working exploit against someone's live instance.
