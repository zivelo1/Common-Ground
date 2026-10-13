# Publication privacy audit

## Scope

This review covers the exact public files listed in PUBLIC_FILES.txt and fingerprinted in PUBLIC_MANIFEST.json. It covers application and provider code, selected tests, static assets, documentation, fictional examples, screenshots, launchers, and example configuration.

The application keeps real instance data locally. The public repository excludes actual configuration, credentials, browser sessions, databases, messages, uploads, backups, logs, runtime files, test evidence, environment files, caches, and private source material. These excluded files may remain in a developer's local folder. **Do not upload the whole folder.**

## Changes made for publication

- Replaced private machine paths with portable project-relative launch and test paths.
- Removed personal and private-project references from code comments, test fixtures, and asset acquisition metadata.
- Ported the current reviewed application features into this standalone project.
- Kept generic collaboration rules and complete user, configuration, integration, architecture, security, and publishing documentation.
- Used browser-local message dates and an explicit UTC example daily-check schedule.
- Added exclusions for actual configuration and runtime data.
- Preserved provider attribution, hashes, the upstream icon license, and trademark notices.
- Created screenshots from fictional museum coordination fixtures with every browser request intercepted.

## Checks

Run python scripts/publication_check.py before staging files. It rejects private paths, symlinks, missing or modified files, disagreement between the file list and manifest, and several high-confidence credential patterns. If a Git index exists, tracked files outside the list are refused.

The final review also checks public text for identifying project references, validates example configuration, reviews screenshot pixels and metadata, and checks document links. The exact final package and its hashes must be reviewed again if any file changes.

No Git history existed when this package was first prepared. This does not certify future commits. Before changing repository visibility, inspect every tracked file and all earlier commits. Deleting a file in a later commit does not remove it from earlier history.

## Six-addition preparation review — 13 October 2026

An independent disposable generic fixture combined the five communication units with the exact provider patch. Its original combined source passed 320 application/browser tests with no skips. This reproduced the author's combined-suite count. It is a baseline check of those bytes, not acceptance of later permission, message-fetch or documentation corrections.

That review found a private attribution in a provider test, a browser-fixture typing issue, stale publication metadata and stale security wording. A separate permission review found that a removed human could retain provider access through an existing runner. These findings required corrections and final checks bound to the completed candidate. The earlier passing count does not cover those corrections. Final publication review must use the exact corrected source and manifest, not this original baseline count.

The nine existing screenshots were independently inspected as fictional museum illustrations and had no embedded PNG metadata. Local documentation links resolved in that prepared fixture. Pattern scans cannot establish that every sensitive detail is absent. New source and documentation changes need a fresh exact manifest and final review; no publication or instance restart is certified by this document.

## Historical prepared package review — 12 October 2026

The prepared public source passed 264 application and browser tests. Final changes to browser-local dates, portable test support, and publication boundaries passed the affected checks. The upload checker passed ten synthetic controls, including private paths, linked files, altered files, incomplete staging, and older staged content. Ruff checks for errors and import ordering passed. Type checks passed for the explicitly selected application and publication files; this is not a claim of exhaustive typing.

All nine PNGs were visually reviewed and have no embedded metadata. Their museum names, dates, records, and outcomes are fictional. The screenshot generator intercepted all browser requests and reported no external requests or page errors. This illustrates the interface; it does not validate a real museum database, remote provider, or physical microphone.

The exact candidate text scan found no personal/private-project identifiers, private machine paths, or high-confidence credential-pattern matches. All local documentation links resolved within the package. These results apply to the reviewed allowlist, not to adjacent excluded files or future changes.

The independent preparation review resolved its source, documentation, and publication-boundary findings.

## Limits

Automated patterns and human review reduce risk; they cannot guarantee the absence of all sensitive material. A local application privacy review is not a penetration test. The shared record system does not guarantee agent memory, understanding, obedience, or secure isolation from other software running as the same operating-system user.

Optional remote providers can send permitted context outside the machine. Backups, exports, logs, and copied screenshots may contain real project data after normal use. Keep those artifacts private and review them separately before sharing.

Real microphone hardware has not been tested for this release. Offline browser checks use synthetic devices or generated audio. File ownership records coordinate work; they do not prevent direct filesystem writes.

See [Security](../SECURITY.md), [Publishing](PUBLISHING.md), and [third-party notices](../THIRD_PARTY_NOTICES.md).
