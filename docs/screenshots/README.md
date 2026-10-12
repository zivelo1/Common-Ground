# Screenshot gallery

These images show the real Common Ground interface with an entirely fictional **Aurora Museum** exhibition project. The names, dates, tasks, decisions, source references, message receipts and outcomes are demonstration fixtures. They do not represent real people, organisations or agent activity.

The generator intercepts every browser request and serves the local application assets. It does not start an application server, read a real configuration or database, call a model provider, or request microphone access. The example source paths are fictional references; the screenshots do not prove that those files were indexed.

## Views

| Image | What it shows |
| --- | --- |
| [Conversation](conversation.png) | Formatted messages, named participants, composer tools and read markers |
| [Message receipts](receipts.png) | Separate received and read states, with the attention limitation |
| [Tasks](tasks.png) | Ownership, priorities, due dates, filters and progress |
| [Decisions](decisions.png) | Proposals, human approval, implementation and independent verification |
| [Joining brief](joining-brief.png) | Shared context, source freshness, review counts and daily maintenance |
| [Knowledge](knowledge.png) | Evidence, lessons and source citations |
| [Stale knowledge](knowledge-stale.png) | A retained older claim and its changed source |
| [Rules](rules.png) | Human-edited rules and preserved version history |
| [Mobile conversation](mobile.png) | The responsive conversation interface |

## Reproduce

With Playwright and its Chromium browser already installed in your development environment:

```sh
python tests/generate_demo_screenshots.py
```

The input is [museum-demo.json](../../examples/museum-demo.json). Screenshots are written here. Browser temporary files and the validation report are confined to ignored `state/demo-qa/`. The report records application and fixture fingerprints, every visible text snapshot, request assertions, page errors and image dimensions.

These are UI illustrations. They do not certify backend access control, real agent understanding, hardware microphone recording or provider integration.
