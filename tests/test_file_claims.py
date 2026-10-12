"""File ownership claims: task owners record a file or tree claim within a configured source root. Claims are coordination records with attributed notifications; they cannot prevent direct filesystem writes."""
import json
import os
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_backend

import cli
import mcp
from store import APIError, Store, load_config


class FileClaimTests(unittest.TestCase):
    # Reuse the backend server fixture's methods. Never subclass or bind its TestCase class here: the loader would
    # collect and run every backend test a second time.
    setUp_backend = test_backend.BackendTests.setUp
    start = test_backend.BackendTests.start
    stop = test_backend.BackendTests.stop
    tearDown = test_backend.BackendTests.tearDown
    call = test_backend.BackendTests.call
    ok = test_backend.BackendTests.ok

    def setUp(self):
        self.setUp_backend()
        (self.source_root / "work").mkdir()
        self.configure()

    def configure(self, cap=20, extra_rules=None, enabled=True):
        self.stop()
        raw = json.loads(self.config.read_text())
        raw.update(source_roots=[{"id": ident, "path": str(self.source_root), "git": False,
                                  "exclude": ["work/excluded", "work/Café"]} for ident in ("fixture", "alias")],
                   source_files=[])
        if enabled:
            raw["work_permissions"] = {"schema_version": 1, "max_active_claims": cap, "rules": [
                {"root_id": ident, "channel_id": "workspace", "path": "work", "kind": "tree"} for ident in ("fixture", "alias")]
                + (extra_rules or [])}
        else:
            raw.pop("work_permissions", None)
        self.config.write_text(json.dumps(raw))
        self.start()

    def task(self, owner="a", channel="workspace"):
        return self.ok("POST", "/api/tasks", owner, {"channel_id": channel, "title": "Synthetic path work",
                                                     "body": "Coordinate scoped edits", "owner_id": owner})

    def payload(self, task, path="work/item.txt", kind="file", **kwargs):
        return {"task_id": task["id"], "task_version": task["version"], "root_id": "fixture", "path": path, "kind": kind,
                "client_id": uuid4().hex, **kwargs}

    def claim(self, task, path="work/item.txt", kind="file", **kwargs):
        return self.ok("POST", "/api/file-claims", task["owner_id"], self.payload(task, path, kind, **kwargs))

    def release(self, row, actor="a", reason="Synthetic reviewed handoff"):
        return self.ok("POST", "/api/file-claims/" + row["id"] + "/release", actor, {"version": row["version"], "reason": reason})

    def test_owner_claim_retry_preserves_exact_record_and_history(self):
        task = self.task()
        payload = self.payload(task)
        row = self.ok("POST", "/api/file-claims", "a", payload)
        self.assertEqual(self.ok("POST", "/api/file-claims", "a", payload), row)
        self.assertEqual(len(self.ok("GET", "/api/file-claims/" + row["id"] + "/history", "a")["history"]), 1)
        self.assertEqual(self.call("POST", "/api/file-claims", "a", {**payload, "path": "work/different.txt"})[0], 409)
        self.assertNotIn(str(self.source_root), json.dumps(row))

    def test_claims_and_releases_notify_the_task_conversation_once(self):
        # Collaboration: "notify other participants". One attributed message per claim and per release; retries add none.
        task = self.task()
        payload = self.payload(task)
        row = self.ok("POST", "/api/file-claims", "a", payload)
        self.ok("POST", "/api/file-claims", "a", payload)                       # the same request again
        released = self.release(row, "human", "Review finished")
        self.release(row, "human", "Review finished")                           # the same release again
        seen = [(message["author_id"], message["body"]) for message in
                self.ok("GET", "/api/messages?channel_id=workspace", "b")["messages"]]
        self.assertEqual(seen, [
            ("a", "File ownership: claimed single file fixture:work/item.txt for task \u201cSynthetic path work\u201d."),
            ("human", "File ownership: released single file fixture:work/item.txt for task \u201cSynthetic path work\u201d."
                      " Reason: Review finished")])
        self.assertEqual(released["status"], "released")
        self.assertNotIn(str(self.source_root), json.dumps(seen))

    def test_only_task_owner_can_claim_and_only_owner_can_check(self):
        task = self.task()
        self.assertEqual(self.call("POST", "/api/file-claims", "b", self.payload(task))[0], 403)
        self.assertEqual(self.call("POST", "/api/file-claims", "human", self.payload(task))[0], 403)
        row = self.claim(task)
        self.assertEqual(self.call("POST", "/api/file-claims/" + row["id"] + "/check", "b", {"version": row["version"]})[0], 403)
        checked = self.ok("POST", "/api/file-claims/" + row["id"] + "/check", "a", {"version": row["version"]})
        self.assertEqual(checked["claim"], row)
        self.assertNotIn(str(self.source_root), json.dumps(checked))

    def test_file_tree_overlap_and_root_aliases_refuse_collisions(self):
        task = self.task()
        other = self.task("b")
        self.claim(task, "work/folder", "tree")
        for path, kind, root in (("work/folder/item.txt", "file", "fixture"), ("work", "tree", "fixture"),
                                 ("work/folder", "tree", "alias")):
            with self.subTest(path=path, kind=kind, root=root):
                self.assertEqual(self.call("POST", "/api/file-claims", "b", self.payload(other, path, kind, root_id=root))[0], 409)
        self.claim(other, "work/folder-name.txt")

    def test_concurrent_separate_store_instances_admit_one(self):
        first = self.task()
        second = self.task("b")
        stores = [Store(load_config(self.config)), Store(load_config(self.config))]
        barrier = threading.Barrier(2)

        def attempt(index):
            barrier.wait(timeout=3)
            task = (first, second)[index]
            actor = {"id": task["owner_id"], "kind": "agent", "name": "Synthetic"}
            try:
                stores[index].claim_file(actor, self.payload(task))
                return 200
            except APIError as error:
                return error.status

        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(attempt, range(2)))
        self.assertEqual(sorted(result), [200, 409])

    def test_case_and_unicode_aliases_conservatively_conflict(self):
        first = self.task()
        second = self.task("b")
        self.claim(first, "work/Café.txt")
        for path in ("work/café.txt", "work/Café.txt"):
            self.assertEqual(self.call("POST", "/api/file-claims", "b", self.payload(second, path))[0], 409)

    def test_no_automatic_expiry_or_done_release(self):
        task = self.task()
        row = self.claim(task)
        self.ok("POST", "/api/tasks/" + task["id"] + "/status", "a", {"status": "done", "version": task["version"]})
        self.assertEqual(self.ok("GET", "/api/file-claims?task_id=" + task["id"], "a")["claims"][0], row)
        self.assertEqual(self.call("POST", "/api/file-claims/" + row["id"] + "/check", "a", {"version": row["version"]})[0], 409)
        other = self.task("b")
        self.assertEqual(self.call("POST", "/api/file-claims", "b", self.payload(other))[0], 409)

    def test_explicit_release_is_owner_or_human_versioned_retry_safe(self):
        task = self.task()
        row = self.claim(task)
        endpoint = "/api/file-claims/" + row["id"] + "/release"
        self.assertEqual(self.call("POST", endpoint, "b", {"version": 1, "reason": "No authority"})[0], 403)
        self.assertEqual(self.call("POST", endpoint, "a", {"version": 2, "reason": "Stale"})[0], 409)
        self.assertEqual(self.call("POST", endpoint, "human", {"version": 1, "reason": ""})[0], 400)
        released = self.release(row, "human")
        self.assertEqual(self.release(row, "human"), released)
        self.assertEqual(self.call("POST", endpoint, "human", {"version": 1, "reason": "Different"})[0], 409)
        history = self.ok("GET", "/api/file-claims/" + row["id"] + "/history", "a")["history"]
        self.assertEqual([item["action"] for item in history], ["created", "released"])
        self.assertEqual(history[1]["actor_id"], "human")

    def test_released_request_never_reacquires_and_new_handoff_can_claim(self):
        task = self.task()
        payload = self.payload(task)
        row = self.ok("POST", "/api/file-claims", "a", payload)
        released = self.release(row)
        self.assertEqual(self.ok("POST", "/api/file-claims", "a", payload), released)
        other = self.task("b")
        self.claim(other)

    def test_private_claim_overlap_error_hides_owner_and_task(self):
        private = self.ok("POST", "/api/channels", "human", {"title": "Synthetic hidden project", "members": ["a"]})["id"]
        self.configure(extra_rules=[{"root_id": "fixture", "channel_id": private, "path": "work", "kind": "tree"}])
        hidden_task = self.task("a", private)
        row = self.claim(hidden_task)
        other = self.task("b")
        status, response, _headers = self.call("POST", "/api/file-claims", "b", self.payload(other))
        self.assertEqual(status, 409)
        for value in (hidden_task["id"], row["id"], private, "Synthetic hidden project"):
            self.assertNotIn(value, json.dumps(response))
        self.assertEqual(self.call("GET", "/api/file-claims?task_id=" + hidden_task["id"], "b")[0], 404)
        self.assertEqual(self.call("GET", "/api/file-claims/" + row["id"] + "/history", "b")[0], 404)
        self.assertEqual(self.call("POST", "/api/file-claims/" + row["id"] + "/release", "b", {"version": 1, "reason": "No"})[0], 404)

    def test_removed_membership_blocks_claim_check_history(self):
        task = self.task()
        row = self.claim(task)
        with self.server.store.connection(write=True) as db:   # the test's own disposable database
            db.execute("DELETE FROM members WHERE channel_id='workspace' AND participant_id='a'")
        for method, path, body in (("POST", "/api/file-claims", self.payload(task)),
                                   ("GET", "/api/file-claims/" + row["id"] + "/history", None),
                                   ("POST", "/api/file-claims/" + row["id"] + "/check", {"version": 1})):
            self.assertEqual(self.call(method, path, "a", body)[0], 404)

    def test_unsafe_sealed_excluded_and_traversal_paths_are_refused(self):
        task = self.task()
        bad = ["/absolute.txt", "work/../escape.txt", "work/./x.txt", "work//x.txt", "work/CLAUDE.md", "work/.claude/rules.md",
               "work/docs/Claude_Rules/rules.md", "work/state/data.txt", "work/credentials/token.json", "work/sealed-x/data.txt",
               "work/file.db", "work/.env", "work/excluded/file.txt", "elsewhere/file.txt"]
        for path in bad:
            with self.subTest(path=path):
                self.assertIn(self.call("POST", "/api/file-claims", "a", self.payload(task, path))[0], (400, 403))
        self.assertFalse((self.source_root / ".claude").exists())

    def test_excluded_case_and_unicode_aliases_cannot_bypass_permission(self):
        task = self.task()
        for path in ("work/EXCLUDED/item.txt", "work/Excluded/item.txt", "work/Café/item.txt"):
            with self.subTest(path=path):
                self.assertEqual(self.call("POST", "/api/file-claims", "a", self.payload(task, path))[0], 403)

    def test_symbolic_and_hardlinked_targets_cannot_bypass_boundary(self):
        task = self.task()
        target = self.source_root / "work/normal.txt"
        target.write_text("Public text fixture")
        (self.source_root / "work/link.txt").symlink_to(target)
        os.link(target, self.source_root / "work/hard.txt")
        for path in ("work/link.txt", "work/hard.txt", "work/normal.txt"):
            self.assertEqual(self.call("POST", "/api/file-claims", "a", self.payload(task, path))[0], 403)

    def test_check_refuses_path_swap_and_permission_changes(self):
        task = self.task()
        row = self.claim(task)
        (self.source_root / "work/item.txt").symlink_to(self.source_root / "work/other.txt")
        self.assertEqual(self.call("POST", "/api/file-claims/" + row["id"] + "/check", "a", {"version": 1})[0], 403)
        (self.source_root / "work/item.txt").unlink()
        self.configure(cap=19)
        self.assertEqual(self.call("POST", "/api/file-claims/" + row["id"] + "/check", "a", {"version": 1})[0], 409)
        self.release(row)

    def test_disabled_policy_preserves_release_and_legacy_member_status(self):
        task = self.task()
        row = self.claim(task)
        self.configure(enabled=False)
        self.assertFalse(self.ok("GET", "/api/work-permissions?task_id=" + task["id"], "a")["configured"])
        self.assertEqual(self.call("POST", "/api/file-claims", "a", self.payload(task, "work/other.txt"))[0], 403)
        self.assertEqual(self.ok("GET", "/api/file-claims?task_id=" + task["id"], "a")["claims"], [row])
        self.release(row)
        self.ok("POST", "/api/tasks/" + task["id"] + "/status", "b", {"version": task["version"], "status": "working"})

    def test_active_cap_and_bounded_cursor_pages(self):
        self.configure(cap=3)
        task = self.task()
        rows = [self.claim(task, "work/item-" + str(index) + ".txt") for index in range(3)]
        self.assertEqual(self.call("POST", "/api/file-claims", "a", self.payload(task, "work/fourth.txt"))[0], 409)
        first = self.ok("GET", "/api/file-claims?task_id=" + task["id"] + "&limit=2", "a")
        second = self.ok("GET", "/api/file-claims?task_id=" + task["id"] + "&limit=2&before=" + first["next_before"], "a")
        self.assertEqual({row["id"] for row in first["claims"] + second["claims"]}, {row["id"] for row in rows})
        self.assertIsNone(second["next_before"])
        self.assertEqual(self.call("GET", "/api/file-claims?task_id=" + task["id"] + "&limit=201", "a")[0], 400)
        other = self.task()
        self.assertEqual(self.call("GET", "/api/file-claims?task_id=" + other["id"] + "&before=" + rows[0]["id"], "a")[0], 404)

    def test_invalid_payloads_are_bounded_errors_not_500(self):
        task = self.task()
        original = self.payload(task)
        invalid = (None, True, [], {}, "", "\ud800")
        for key in original:
            for value in invalid:
                with self.subTest(key=key, value_type=type(value).__name__):
                    status = self.call("POST", "/api/file-claims", "a", {**original, key: value})[0]
                    self.assertIn(status, (400, 403, 404, 409))
        self.assertEqual(self.call("POST", "/api/file-claims", "a", {**original, "owner_id": "b"})[0], 400)

    def test_unavailable_path_metadata_is_bounded_without_path_disclosure(self):
        task = self.task()
        payload = self.payload(task)
        resolve = Path.resolve

        def denied(path, *args, **kwargs):
            if path == self.source_root / payload["path"]:
                raise PermissionError("Synthetic unavailable work metadata")
            return resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", denied):
            status, result, _headers = self.call("POST", "/api/file-claims", "a", payload)
        self.assertEqual(status, 503)
        self.assertNotIn(str(self.source_root), json.dumps(result))
        self.assertEqual(self.ok("GET", "/api/file-claims?task_id=" + task["id"], "a")["claims"], [])

    def test_restart_keeps_claims_drafts_and_outbox(self):
        # The proposal's rollback half (yesterday's Store reading the migrated database) needs the previous source, so it
        # is a one-time unit check (port/rollback_check.py), not a permanent test.
        task = self.task()
        row = self.claim(task)
        self.ok("POST", "/api/personal/draft", "a", {"channel_id": "workspace", "body": "Synthetic unsent text", "version": 0})
        self.ok("POST", "/api/personal/enqueue", "a", {"channel_id": "workspace", "body": "Synthetic queued text", "client_id": "keep"})
        personal = self.ok("GET", "/api/personal", "a")
        self.stop()
        self.start()
        self.assertEqual(self.ok("GET", "/api/file-claims?task_id=" + task["id"], "a")["claims"], [row])
        self.assertEqual(self.ok("GET", "/api/personal", "a"), personal)

    def test_mcp_and_existing_cli_preserve_scope_and_owner(self):
        task = self.task()
        payload = self.payload(task)
        row = mcp.call(self.config, "a", "collab_claim_file", payload)
        self.assertEqual(mcp.call(self.config, "a", "collab_check_file_claim", {"id": row["id"], "version": 1})["claim"], row)
        self.assertEqual(cli.request(self.config, "a", "GET", "/api/file-claims?task_id=" + task["id"])["claims"], [row])
        with self.assertRaises(ValueError):
            mcp.call(self.config, "a", "collab_check_file_claim", {"id": "../../credentials", "version": 1})
        with self.assertRaises(cli.RequestError):
            mcp.call(self.config, "b", "collab_release_file_claim", {"id": row["id"], "version": 1, "reason": "No authority"})

    def test_new_routes_retain_host_origin_csrf_guards(self):
        task = self.task()
        for headers in ({"Origin": "https://foreign.invalid"}, {"Host": "foreign.invalid"}, {"X-Collab-Request": ""}):
            self.assertEqual(self.call("POST", "/api/file-claims", "a", self.payload(task), headers=headers)[0], 403)
        self.assertEqual(self.call("GET", "/api/file-claims?task_id=" + task["id"] + "&unexpected=x", "a")[0], 400)


if __name__ == "__main__":
    unittest.main()
