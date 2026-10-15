# Project boundaries: proposed external enforcement

Status: design for review. No operating-system account, permission, network, tool, provider, or running instance is changed by this document. No containment test has been run for this design. The current app boundary is documented in [Architecture](ARCHITECTURE.md) and [Security](../SECURITY.md).

## Problem and decisive distinction

Application roles protect normal API requests. A process with the same operating-system account may bypass that API and use its filesystem or other tools directly. Another app permission check cannot settle this difference.

The decisive future test is an attempted read of a fictional sibling project's canary file through a direct file tool and a child process, while authorized work still succeeds. An API denial alone is insufficient. Never use a real secret as the test target.

Threats in scope are mistaken project selection, malicious document instructions, stolen agent credentials, unauthorized subprocesses, cross-project reads/writes, outbound disclosure, policy tampering, and unrecorded exceptions. A compromised host administrator or virtualization implementation remains outside this proposed guarantee; no option promises perfect prevention.

## Options and costs

| Option | Useful boundary | Remaining gap and usability cost |
| --- | --- | --- |
| Current same-account app roles and file claims | Membership, identity, approval and coordination within the app | Direct filesystem/tool access remains outside the checks. Lowest setup cost; no process containment claim. |
| Separate non-administrator account with deliberate file permissions | A distinct operating-system identity for each authorized project or agent group | Shared folders, accessible credentials, network access and desktop tools still need controls. Account switching and client support add work. Apple distinguishes standard and administrator capabilities in its [Users & Groups guide](https://support.apple.com/guide/mac-help/change-users-groups-settings-mtusr001/mac). |
| Restricted container with narrow mounts and managed egress | A separate runtime and explicitly exposed project files | Broad host mounts or runtime-control sockets can defeat the boundary. Desktop clients may not fit. Docker documents a separately managed [enhanced isolation mode](https://docs.docker.com/desktop/enterprise/hardened-desktop/enhanced-container-isolation/); this does not establish that a local installation uses it. |
| Dedicated virtual machine with explicit exchange | A guest system with a deliberate host/guest boundary | More storage, memory, setup and integration. Shares, clipboard and network must be deliberate. Apple exposes [specific shared directories](https://developer.apple.com/documentation/virtualization/shared-directories) and [read-only directory settings](https://developer.apple.com/documentation/virtualization/vzshareddirectory); a virtual machine alone does not prove host data is inaccessible. |

The proposed pilot is a dedicated guest with a narrow exchange path when the priority is resistance to direct host-tool access. This is a design inference from the threat model, not a measured product comparison. A separate account is a lower-friction alternative only if its tested limits satisfy the owner. No product selection or installation is approved here. Compatibility, resource use and workflow time remain unmeasured.

## Proposed control path

1. The human administrator owns a policy outside agent-writable directories and guest control. Agents, including the implementing coding agent, cannot modify the policy, launcher, exception ledger, approval interface or audit destination.
2. An administrator-controlled launcher assigns project, identity, immutable policy version and resource budget. It exposes only a dedicated worktree and scratch area. Private app databases and human credentials stay outside agent mounts; conversation access uses the scoped app API.
3. Reads and writes default to denied outside selected roots. Traversal, symbolic links, inherited file descriptors and child processes must not create another route. Agents receive no administrator credential, runtime-control socket or unrestricted host shell.
4. Tools run through the constrained runtime; shell children inherit the boundary. An external launcher or gateway checks executable paths, arguments, working directory, environment and input/output limits. Agent-writable wrappers cannot grant broader capabilities.
5. Network access defaults to denied. A human-controlled gateway grants a destination and action only when authorized. It checks redirects, alternate addresses and local network destinations. Provider credentials stay in the gateway, scoped per agent/project; agents never receive the human's general credential.
6. A sensitive exception names project, identity, exact operation, paths/destination, purpose, expiry, maximum uses and recovery. The human sees the concrete action outside the agent-writable UI. One-use approval binds the canonical request fingerprint and policy version; altered/replayed requests are refused.
7. Admission, denial, start, result, uncertainty and recovery events go to an append-only destination the agent cannot edit. Store hashes and necessary redacted metadata, not secrets or private bodies in shared logs. Gateway failure fails closed. Retention is a human decision.
8. Revocation prevents new admission and cancels controllable in-flight work, recording effects that may already have escaped. The owner can stop the runtime, preserve evidence, discard its worktree and restore a verified baseline without rewriting conversation history.

This path covers all participating clients. Moving only one module while another retains unrestricted host tools does not establish a project-wide boundary. Inventory desktop-client permissions before a pilot; this document neither inspects nor changes them.

## Acceptance before any containment claim

Use two fictional projects and synthetic identities. Record runtime/operating-system versions, policy bytes/hash, mounts, egress, credential scopes, exact probes, events and independent observer. The observer controls canaries and checks outcomes outside the runtime. Repeat after policy changes and restarts.

| Probe | Required observed outcome |
| --- | --- |
| Normal edit/test in admitted worktree | Allowed; attributable result and recoverable diff |
| Sibling read/write through file tool, child, traversal or symlink | Denied; outside canary unchanged; denial metadata scoped/redacted |
| Direct app database/human credential access | Denied through every exposed host/guest/tool route |
| Unlisted destination, redirect or local service | Denied externally; no observed receiving-side request |
| Agent modifies policy, launcher, audit or permissions | Denied; administrator-measured bytes/history unchanged |
| Changed request, expired/replayed approval, wrong identity/project | Denied without an external effect |
| Child, restart, inherited environment or stale approval | Same limits; no privilege survives revocation |
| Removal during work or gateway/audit failure | New admission denied; uncertain in-flight effects preserved |
| Recovery | Verified baseline restored in a disposable target; failed evidence retained |
| Each client, including the author | Same measured boundary; an unrestricted client blocks the broad claim |

Mocked return codes cannot pass these probes. Include outside observations and a useful allowed control. An essential skip, leak, editable policy/audit sink or untested client leaves the pilot unvalidated.

## Decision and delivery boundary

The human chooses threat model, runtime/account option, exchange, services, exception owner and retention after review and independent challenge. A later implementation package must name exact launcher/gateway files, policy schema, migration/recovery and measured compatibility before an account, tool or network change. Those implementation files do not exist in this preparation; this document is the reviewable design deliverable.

Bottom line: app permissions cannot enforce a boundary against a process that bypasses the app. External controls must pass direct-access tests before a stronger claim is justified.

Action items: human owner — choose the intended boundary and approve a concrete pilot; implementing participant — prepare that implementation after the decision; reviewer — challenge the design and eventual pilot; watch — same-account access and untested clients remain outside containment.
