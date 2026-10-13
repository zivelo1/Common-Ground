# Publishing a public package

Publish a deliberately selected source package. A private repository is useful for review, but confidential files must stay out of it too. Making a repository public exposes its reachable history, not just the current file tree.

These instructions are for the maintainer to run. Preparing the package does not itself create a repository, commit, push, or change visibility.

## 1. Preserve licensing and attribution

The project uses the [MIT License](../LICENSE). Include its actual text and preserve the required copyright and permission notice. GitHub explains repository licensing in its [licensing guide](https://docs.github.com/en/repositories/creating-and-managing-repositories/licensing-a-repository).

### Licensing and attribution

Keep [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md), bundled asset provenance, and applicable license notices with the assets. Provider logos need their recorded origin, version, and license terms. Preserve the upstream copyright notice. An application license does not replace an asset's license or grant trademark rights; attribution does not imply endorsement.

Review dependency licenses separately. Do not invent a copyright holder or permission statement. Public provenance should retain verifiable public origins and fingerprints without private acquisition notes.

## 2. Inspect the explicit upload manifest

The final package uses `PUBLIC_FILES.txt` as an exact relative-path allowlist and `PUBLIC_MANIFEST.json` as its byte-count and SHA-256 record. The manifest does not hash itself; it is explicitly included in the upload list. Freeze these after the final review and review again if any candidate bytes change.

| Category | Candidate files |
| --- | --- |
| Application | `app.py`, `store.py`, `attachments.py`, `backups.py`, `dictation.py`, `launch.py`, `cli.py`, `mcp.py`, `adapter_controller.py`, `adapter_runner.py` |
| Provider code | Individually listed source files under `providers/` |
| Browser assets | Individually listed HTML, JavaScript, CSS, icons, and reviewed logo assets/provenance under `static/` |
| Safe templates and dependencies | `config.example.json`, `providers.example.json`, `requirements.txt`, `requirements-dev.txt` |
| Portable entry points | Reviewed `start.command` and `stop.command` |
| Documentation | The public guides, README, contribution/security/change documents, generic `RULES.txt`, privacy audit, and screenshot gallery |
| Fictional examples and images | Individually reviewed museum fixtures and nine screenshots under `docs/screenshots/` |
| Verification source | Individually reviewed synthetic tests and portable support/generator code under `tests/` |
| Release metadata | `.gitignore`, the file list and manifest, MIT `LICENSE`, `THIRD_PARTY_NOTICES.md`, and `scripts/publication_check.py` |

The table describes categories. The actual final manifest must list every file explicitly, without directory or wildcard entries. Do not copy older preview suites or fixtures without reviewing them.

Never include actual `config.json`, provider/human-policy local configurations, state directories, databases and SQLite side files, credentials, browser profiles, backups, logs, exports, private source documents, test receipts, incident evidence, caches, virtual environments, or patch artifacts such as `*.orig` and `*.rej`. Private historical screenshots remain local evidence; producing public illustrations does not justify deleting incident history.

`.gitignore` is a second barrier. It is not an upload manifest and does not remove a file already tracked by Git.

## 3. Audit the candidate

Before upload:

1. Compare the exact manifest with the tree, including hidden files. Reject links or paths outside the package.
2. Scan candidate text for credentials, token/key patterns, real project or personal names, emails, machine paths, private URLs, and internal identifiers.
3. Inspect all nine screenshots, including visible account information, browser chrome, tooltips, filenames, and metadata. Use only the fictional museum workspace.
4. Confirm that test fixtures are synthetic and do not target a private repository, real project, or live service.
5. Keep example providers disabled with no credential values. Check source selections and work-policy examples deliberately.
6. Review licenses, notices, and documentation links.
7. For an existing Git repository, inspect tracked files and reachable history. Deleting a current file does not remove its past versions.

Report a suspected credential by path, line, and category without copying its value. Stop publication and have the owner rotate or revoke any exposed credential. Removing one line does not repair a history leak. See GitHub's [secret scanning documentation](https://docs.github.com/en/code-security/secret-scanning/introduction/about-secret-scanning).

The local publication checker validates the path list, fingerprints, selected credential patterns, and tracked-file allowlist. It does not certify every secret is absent, inspect all historical Git objects, or replace a human visual/source audit. Run it against the final frozen package:

```sh
python3 -B scripts/publication_check.py
```

Stop if it fails. Have the maintainer regenerate and review the manifest after an intended change; do not bypass a mismatch.

For the six additions prepared on 13 October, include the individually reviewed dictation, provider and test files as well as changed existing files. Review JavaScript test files explicitly too. Do not reuse an earlier manifest that omits these additions. After all source and documentation corrections, write the sorted exact file list first, then record every listed file's byte count and SHA-256 except the manifest itself. The list includes both `PUBLIC_FILES.txt` and `PUBLIC_MANIFEST.json`. Run the checker against those final bytes.

The checker is a manifest/index check. It does not reject every unlisted file in a local working folder, inspect all Git history, or guarantee that every identifying detail is absent. Publish only the exact reviewed list, never the whole folder. A passing earlier suite is historical evidence; it does not validate corrections made later. Preserve the original preparation receipts privately and bind final checks to the actual release candidate.

## 4. Create an empty private GitHub repository

Follow GitHub's [new repository guide](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository):

1. Choose the intended GitHub account.
2. Name the repository **Common-Ground**.
3. Set visibility to **Private**.
4. Leave initialization with a README, license, or ignore template unchecked. Those files already belong to the reviewed local package.
5. Create the repository and copy its HTTPS repository address.

Keep the repository description generic. Authenticate through your normal GitHub client; do not put an access token in a command or public file.

## 5. Set public commit identity and stage exact paths

From the canonical application folder, for a fresh package that has no Git repository:

```sh
git init -b main
```

If Git history already exists, audit it first and use that reviewed repository deliberately. Do not erase or reinitialize history to make an audit appear clean.

Commit author names and email addresses become public metadata. Choose a public commit name and use the GitHub-provided no-reply email shown in your account's email settings. GitHub documents this in [Setting your commit email address](https://docs.github.com/en/account-and-profile/setting-up-and-managing-your-personal-account-on-github/managing-email-preferences/setting-your-commit-email-address). Replace these placeholders with your chosen public values; do not leave them literal:

```sh
git config user.name "YOUR_PUBLIC_COMMIT_NAME"
git config user.email "YOUR_GITHUB_NOREPLY_EMAIL"
python3 -B scripts/publication_check.py
```

Stage only the exact approved list through argument-safe Python subprocess invocation:

```sh
python3 - <<'PY'
from pathlib import Path
import subprocess

paths = Path("PUBLIC_FILES.txt").read_text(encoding="utf-8").splitlines()
if not paths or any(not path for path in paths):
    raise SystemExit("The reviewed public file list is missing or empty")
subprocess.run(["git", "add", "--", *paths], check=True)
PY
```

Inspect the staged path set and diff, then validate the actual Git index against the reviewed bytes:

```sh
git status --short
git diff --cached --name-only
git diff --cached
python3 -B scripts/publication_check.py --staged
```

Confirm that the staged set matches `PUBLIC_FILES.txt` and contains no other material. The staged checker checks index blobs against audited file fingerprints and rejects conflicting or linked entries. Review any untracked or previously tracked private files separately; do not stage them merely to make the working tree look clean. Stop if either the diff or checker reveals a mismatch.

## 6. Commit and push to the private repository

Only after the staged review passes:

```sh
git commit -m "Prepare Common Ground public package"
git remote add origin https://github.com/YOUR_USERNAME/Common-Ground.git
git push -u origin main
```

Replace `YOUR_USERNAME` with the intended account name. If `origin` already exists, inspect its destination before using it; do not overwrite an unrelated remote. These commands upload only the committed, reviewed package. They do not change visibility.

Compare the private uploaded tree and reachable history with the manifest. Inspect rendered documentation, relative links, all nine fictional screenshots, MIT text, and third-party notices.

Run the agreed verification locally against the exact candidate in disposable private state. A historical pass from another source tree does not certify the uploaded bytes. Keep generated logs, profiles, databases, and receipts outside the repository. Distinguish automated browser checks from a physical microphone check.

## 7. Complete independent review before making it public

Obtain the maintainer's designated independent review of the completed package. Send only the same audited manifest package, with authorization for the intended destination.

Resolve findings or document a reasoned acceptance. An unresolved private-data leak or missing required notice blocks publication. If review changes package bytes, update/review the manifest, rerun the checker, and push the reviewed correction while the repository is still private.

After the audit and maintainer approval, follow GitHub's [visibility instructions](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility): open the intended repository's **Settings**, find **Danger Zone**, select **Change repository visibility**, and choose **Public**. Read and complete the confirmation shown by GitHub at that time.

Public content can be copied or forked. A later visibility change cannot reliably recover disclosed material. Inspect the public repository and share only its verified public link.

## 8. Build a collaborator archive from the same list

If review needs an archive, build it from the final explicit manifest rather than uploading the folder recursively. Exclude `.git` and private state unless a separately audited Git transfer is the intended artifact.

Check the archive's exact file list and fingerprints. Include the guides, examples, fictional screenshots, and real license terms. The recipient also needs the documented platform/dependency limits. Verify the intended destination and authorization before transfer.
