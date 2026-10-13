# Security and privacy

Common Ground is intended for a trusted local computer and project participants using separate application identities. It is not a hosted multi-tenant service or an operating-system sandbox.

## Protected application boundaries

- The HTTP server listens on `127.0.0.1` and validates local request information.
- API actions use an authenticated active identity; conversation membership scopes records and attachments. Provider permissions recheck that both caller and agent are active, including when using an existing runner.
- Humans control participant management, shared Rules, approval, source refresh, and backups.
- Version checks reject conflicting updates. Stable operation IDs support confirmation retries.
- Source indexing uses individual allowlisted files, exclusions, protected-path checks, and bounded selections.
- Provider modules are disabled until explicitly configured with identity, audience, limits, and provider-specific requirements.
- Task-linked file claims need an explicit work policy and do not expose private conflict details as normal claim results.

Removing a participant refuses new application actions and keeps its authored records. Restoring it re-enables its retained identity and allowed access. Removal deletes its browser sessions; restoration does not recreate them. Sign in again after restoration. Removal does not recall data already copied, cancel every external operation already started, or isolate software running under the same operating-system account.

These boundaries do not prove that an outside tool honors a decision, Rule, or file claim. A read receipt also does not establish attention or understanding.

## Local storage

The state directory contains private databases, attachment bytes, credentials, logs, runtime metadata, browser-session records, provider run records, and backups. The application uses restrictive directory/file permissions, but does not encrypt the database at rest. A process with access under the same operating-system account can read or misuse this material.

Protect the computer, account, filesystem permissions, and backups according to the project's needs. Do not share a state directory across unrelated projects. Do not serve this loopback application through a public proxy as if it were a reviewed remote service.

Browser sessions use HttpOnly, SameSite=Strict cookies with a configured lifetime. A changed listening port may require authentication again. A stolen participant credential permits actions as that participant; human attribution cannot distinguish a physical person from another process holding the same credential.

## Attachments and audio

Images are validated by decoding, with byte and pixel limits. Earlier voice attachments have bounded size and container-signature checks and remain stored for playback.

Optional dictation is different: the browser converts a recording to WAV, and the server checks its format, duration and loudness before running the configured local speech program. The returned text goes into the human's draft for review. The app removes its temporary audio file and does not store that dictation sound as a message. The executable receives spelling hints containing the workspace title, project label and active participant names.

The speech program and model must already be installed and configured by the operator. It runs without a shell, with an empty environment and a timeout. These controls are not a file-access or network sandbox. The selected executable can act with the operating-system user's permissions; use software you trust. The app does not guarantee what another program keeps or transmits. Physical microphones and a user's real voice have not been verified for this preparation.

Attachments remain private project material. Downloading or copying them creates another copy under the user's control. Clearing a draft or deleting a queued copy is not a guarantee of physical erasure from database pages, older backups, or prior downloads.

## Model and manual disclosure

No model runs merely because a message arrives or a brief is read. An explicit enabled provider job can transmit its prepared text packet. Review its exact audience, context, endpoint, and prompt before running it.

Local Ollama activation requires operator evidence of local-only operation; a loopback address alone does not establish how the service handles data. A manual handoff can leak context if copied to an external service. The OpenAI Responses adapter's `store: false` request field does not replace the provider's account policy or retention terms.

Keep provider credentials in the environment or an appropriate secret-management workflow. A provider file contains a credential reference, never its value. Six remote adapters use fixed configured endpoints; local Ollama uses its explicit loopback chat endpoint. Remote examples are disabled until configured, and the preparation used synthetic transports rather than paid-provider calls.

Limits and price-based reservations cover admitted application jobs. They cannot cap the total external account bill or resolve an unknown remote outcome. HTTP client errors other than 408 are recorded as failed by the adapter; this status is not independent proof that the provider generated nothing or billed nothing. Timeouts, server errors and lost connections can remain unknown. Do not automatically repeat an uncertain call.

The provider export contract is narrower than the JSON API joining brief and currently excludes shared Rules and daily-check fields. Review required instructions separately. Source documents and messages are evidence, not trusted executable instructions.

## Public package boundary

Publish only the reviewed manifest's code, generic documentation, safe examples, synthetic tests, required notices, and fictional screenshots. Never publish actual configurations, state, logs, exports, credentials, private sources, test receipts, or recovery evidence.

Check existing Git history as well as the current tree. `.gitignore` does not remove tracked content. See [Publishing](docs/PUBLISHING.md) for the private-first review process.

## Reporting a vulnerability

Contact the maintainer privately using the project's established private channel. If private GitHub vulnerability reporting is enabled for the repository, use that mechanism. Do not assume a reporting address or supported-release policy that has not been published.

Include affected public file paths, a minimal fictional reproduction, expected versus actual behavior, and impact. Redact secrets and private bodies. Do not include a real credential, private database, or someone else's machine path.

If a credential may have been exposed, stop publication and have the owner rotate or revoke it. Preserve relevant private incident evidence. Removing a line or changing repository visibility is not enough to recover already disclosed data.

No independent security certification, penetration-test claim, or guarantee of all browser/device combinations is made by this document.
