"""Real browser checks for the five small conversation upgrades."""
from __future__ import annotations

import io
import json
import os
import threading

import test_conversation_real_backend as fixture
from PIL import Image
from qa_support import evidence_root


class UpgradeBrowserTests(fixture.RealConversationTests):
    def png(self):
        raw = io.BytesIO()
        Image.new("RGB", (300, 180), "royalblue").save(raw, format="PNG")
        return raw.getvalue()

    def test_image_upload_preview_download_send_and_reload(self):
        page = self.page()
        page.locator("#image-file").set_input_files({"name": "chart.png", "mimeType": "image/png", "buffer": self.png()})
        page.wait_for_function("() => !app.uploadingImage && document.querySelector('#message-input').value.includes('/api/images/')")
        page.locator("#send-message").click()
        page.locator(".message-image").wait_for()
        page.wait_for_function("() => document.querySelector('.message-image').naturalWidth === 300")
        page.locator(".image-preview").click()
        page.locator("dialog .full-image").wait_for()
        with page.expect_download() as pending:
            page.locator("dialog a", has_text="Download image").click()
        self.assertEqual(pending.value.suggested_filename, "chart.png")
        page.screenshot(path=str(evidence_root() / "five_upgrades_image_preview.png"), full_page=True)
        page.locator("dialog .icon-button").click()
        page.reload()
        page.locator("#app-shell").wait_for(state="visible")
        page.locator(".message-image").wait_for()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external, [])

    def test_closing_browser_and_restarting_server_keeps_raw_address_signed_in(self):
        profile = self.folder / "remembered_browser"
        token = json.loads((self.folder / "instance_data/credentials/human.json").read_text())["token"]
        original_port = self.server.server_address[1]
        for cycle in range(2):
            context = self.playwright.chromium.launch_persistent_context(str(profile), headless=True,
                env={**os.environ, "TMPDIR": str(self.folder)})
            try:
                def guard(route):
                    if route.request.url.startswith(self.server.origin + "/"):
                        route.continue_()
                    else:
                        self.external.append(route.request.url)
                        route.abort()
                context.route("**/*", guard)
                page = context.pages[0] if context.pages else context.new_page()
                page.on("pageerror", lambda error: self.errors.append(str(error)))
                page.goto(self.server.origin)
                if cycle == 0:
                    page.locator("#access-token").fill(token)
                    page.locator("#login-submit").click()
                page.locator("#app-shell").wait_for(state="visible")
                self.assertTrue(page.locator("#login-screen").is_hidden())
                self.assertEqual(page.locator("#login-error").is_visible(), False)
            finally:
                context.close()
            if cycle == 0:
                self.server.shutdown()
                self.worker.join(timeout=5)
                self.assertFalse(self.worker.is_alive())
                self.server.server_close()
                self.server = fixture.backend.make_server(self.config, original_port)
                self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
                self.worker.start()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external, [])

    def test_paste_adds_an_image_to_the_draft(self):
        page = self.page()
        page.evaluate("""async () => {
          const canvas=document.createElement('canvas');canvas.width=30;canvas.height=20;
          const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
          const transfer=new DataTransfer();transfer.items.add(new File([blob],'pasted.png',{type:'image/png'}));
          document.querySelector('#message-input').dispatchEvent(new ClipboardEvent('paste',{clipboardData:transfer,bubbles:true,cancelable:true}));
        }""")
        page.wait_for_function("() => !app.uploadingImage && document.querySelector('#message-input').value.includes('/api/images/')")
        self.assertIn("pasted.png", page.locator("#message-input").input_value())
        self.assertEqual(self.errors, [])

    def test_copy_reply_and_saved_message_navigation(self):
        message = self.seed(1)[0]
        page = self.page()
        row = page.locator('[data-message-id="' + str(message["id"]) + '"]')
        page.evaluate("() => Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>window.copiedMessage=text}})")
        row.get_by_role("button", name="Copy", exact=True).click()
        self.assertEqual(page.evaluate("window.copiedMessage"), message["body"])
        row.get_by_role("button", name="Reply", exact=True).click()
        self.assertIn("> " + message["author_name"], page.locator("#message-input").input_value())
        self.assertIn("#" + str(message["id"]), page.locator("#message-input").input_value())
        row.get_by_role("button", name="Save ☆", exact=True).click()
        row.get_by_role("button", name="Saved ★", exact=True).wait_for()
        page.locator("#saved-messages").click()
        page.locator(".saved-message-item").click()
        page.locator('.search-highlight[data-message-id="' + str(message["id"]) + '"]').wait_for()
        page.screenshot(path=str(evidence_root() / "five_upgrades_conversation.png"), full_page=True)
        self.assertEqual(self.errors, [])

    def test_image_controls_fit_phone_width(self):
        page = self.page()
        page.set_viewport_size({"width":390,"height":844})
        page.locator("#image-file").set_input_files({"name":"phone.png","mimeType":"image/png","buffer":self.png()})
        page.wait_for_function("() => !app.uploadingImage && document.querySelector('#message-input').value.includes('/api/images/')")
        page.locator("#send-message").click()
        page.locator(".message-image").wait_for()
        self.assertTrue(page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth"))
        page.screenshot(path=str(evidence_root() / "five_upgrades_phone.png"), full_page=True)
        self.assertEqual(self.errors, [])
