import argparse
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.events import MouseMove, MouseScrollDown, MouseScrollUp

from lchat_tui import ChatApp, ChatTableContent, Composer, ModelScreen, SettingsScreen, TABLE_BOX


def arguments(path):
    return argparse.Namespace(endpoint="http://localhost:8070/v1", model="test", api_key="", timeout=10,
        system="system", no_stream=False, temperature=None, max_tokens=None, disable_qwen_thinking=False,
        enter_sends=True, tui_enabled=True, host="127.0.0.1", port=None, config_path=path)


def fake_generate(args, url, events, payload, started):
    events.put(("connected", None))
    events.put(("thinking", "reason"))
    events.put(("token", "Hello"))
    events.put(("done", {"content": "Hello", "thinking": "reason", "metrics": {
        "first_text_seconds": .1, "total_seconds": 1.0, "generation_seconds": .9,
        "tokens_per_second": 10.0, "exact_tokens": True}}))


class TextualTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_history_and_settings(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch("lchat_tui.fetch_models_response", return_value=b'{"data":[{"id":"test"}]}'), \
             patch("lchat_tui.generate", side_effect=fake_generate):
            path = Path(directory) / "config.json"
            app = ChatApp(arguments(path), "http://localhost:8070/v1/chat/completions")
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause(.2)
                await pilot.press("h", "i", "ctrl+j", "x")
                self.assertEqual(app.query_one(Composer).text, "hi\nx")
                await pilot.press("enter")
                await pilot.pause(.3)
                self.assertEqual([item["content"] for item in app.conversation], ["hi\nx", "Hello"])
                await pilot.press("f3")
                self.assertTrue(app.show_thinking)
                await pilot.press("up")
                self.assertEqual(app.query_one(Composer).text, "hi\nx")
                await pilot.press("f2")
                self.assertIsInstance(app.screen, SettingsScreen)
                app.screen.query_one("#save").scroll_visible(animate=False)
                await pilot.pause()
                await pilot.click("#save")
                await pilot.pause(.4)
                self.assertEqual(json.loads(path.read_text())["model"], "test")
                self.assertNotIsInstance(app.screen, SettingsScreen)
                await pilot.press("f4")
                await pilot.pause(.2)
                self.assertIsInstance(app.screen, ModelScreen)
                await pilot.press("escape")
                await pilot.pause()
                app.query_one(Composer).load_text("/reset")
                await pilot.press("enter")
                await pilot.pause()
                self.assertEqual(app.conversation, [])

    async def test_chat_colors_headings_rules_math_and_tables(self):
        def styled_response(args, url, events, payload, started):
            content = ('평문 **강조** 와 $x^2$\n\n# 1단계 제목\n\n## 2단계 제목\n\n### 3단계 제목\n\n---\n\n***\n\n'
                       '| A | B |\n|---|---|\n| 1 | 2 |\n\n\\[x=\\frac{1}{2}\\]\n\n```python\nprint(1)\n```')
            events.put(('done', {'content': content, 'thinking': '', 'metrics': {
                'first_text_seconds': .1, 'total_seconds': 1.0, 'generation_seconds': .9,
                'tokens_per_second': 10.0, 'exact_tokens': True}}))
        with patch('lchat_tui.fetch_models_response', return_value=b'{"data":[{"id":"test"}]}'), patch('lchat_tui.generate', side_effect=styled_response):
            app = ChatApp(arguments('/tmp/nonexistent-lchat-config'), 'http://localhost:8070/v1/chat/completions')
            async with app.run_test(size=(100, 50)) as pilot:
                await pilot.pause(.2)
                await pilot.press('x', 'enter')
                await pilot.pause(.6)
                chat = app.query_one('#chat')
                self.assertEqual(chat.styles.background.hex, '#000000')
                user = app.query_one('.user')
                self.assertEqual(user.styles.background.hex, '#000000')
                self.assertEqual(user.styles.color.hex, '#2E8B57')
                self.assertEqual(user.styles.border.top[0], 'solid')
                self.assertEqual(user.styles.border.top[1].hex, '#2E8B57')
                markdown = app.query_one('Markdown.assistant')
                self.assertEqual(markdown.rich_style.color.triplet.hex, '#aaaaaa')
                self.assertEqual(markdown.get_component_rich_style('strong', partial=True).color.triplet.hex, '#ffffff')
                for level in (1, 2, 3):
                    heading = app.query_one(f'MarkdownH{level}')
                    self.assertEqual(heading.styles.color.hex, '#FFFFFF')
                    self.assertEqual(heading.styles.border.top[1].hex, '#806D20')
                    self.assertEqual(heading.styles.border.left[1].hex, '#806D20')
                    self.assertEqual(heading.styles.content_align_horizontal, 'left')
                rules = list(app.query('MarkdownHorizontalRule'))
                self.assertEqual(len(rules), 2)
                self.assertTrue(all(rule.styles.border.bottom[1].hex == '#808080' for rule in rules))
                math = app.query_one('MarkdownFence.math-block')
                self.assertEqual(math.styles.border.top[1].hex, '#808080')
                self.assertEqual(math.rich_style.color.triplet.hex, '#aaaaaa')
                fences = list(app.query('MarkdownFence'))
                self.assertEqual(len(fences), 2)
                self.assertEqual(sum(fence.has_class('math-block') for fence in fences), 1)
                table_widget = app.query_one(ChatTableContent)
                header_style = table_widget.get_component_rich_style('markdown-table--header')
                self.assertEqual(header_style.color.triplet.hex, '#aaaaaa')
                self.assertFalse(header_style.bold)
                table = table_widget.render()
                self.assertEqual(table.box, TABLE_BOX)
                self.assertTrue(table.show_lines)
                self.assertEqual(TABLE_BOX.top_right, '┒')
                self.assertEqual(TABLE_BOX.bottom, '━')

    async def test_composer_focus_and_compact_settings(self):
        with patch("lchat_tui.fetch_models_response", return_value=b'{"data":[{"id":"test"}]}'), patch("lchat_tui.generate", side_effect=fake_generate):
            app = ChatApp(arguments("/tmp/nonexistent-lchat-config"), "http://localhost:8070/v1/chat/completions")
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause(.2)
                await pilot.press("x", "enter")
                await pilot.pause(.4)
                composer = app.query_one(Composer)
                await pilot.click("MarkdownParagraph")
                await pilot.press("tab")
                self.assertIs(app.focused, composer)
                await pilot.press("f2")
                await pilot.pause()
                self.assertIsInstance(app.screen, SettingsScreen)
                dialog = app.screen.query_one("#settings-dialog")
                for selector in ("#endpoint", "#model", "#timeout", "#temperature", "#host", "#port"):
                    field = app.screen.query_one(selector)
                    self.assertLessEqual(field.region.right, dialog.region.right - 1, selector)
                self.assertLessEqual(app.screen.query_one("#save").region.bottom, dialog.region.bottom - 1)
                await pilot.click("#endpoint")
                self.assertEqual(app.focused.id, "endpoint")
                await pilot.press("escape")
                await pilot.pause()
                self.assertIs(app.focused, composer)

    async def test_quit_shortcuts(self):
        for key in ("ctrl+q", "ctrl+d"):
            for modal in (False, True):
                with self.subTest(key=key, modal=modal), patch("lchat_tui.fetch_models_response", return_value=b'{"data":[{"id":"test"}]}'):
                    app = ChatApp(arguments("/tmp/nonexistent-lchat-config"), "http://localhost:8070/v1/chat/completions")
                    async with app.run_test() as pilot:
                        await pilot.pause(.2)
                        if modal:
                            await pilot.press("f2")
                            self.assertIsInstance(app.screen, SettingsScreen)
                        await pilot.press(key)
                        await pilot.pause()
                        self.assertFalse(app.is_running)

    async def test_mouse_selection_of_response(self):
        with patch("lchat_tui.fetch_models_response", return_value=b'{"data":[{"id":"test"}]}'), patch("lchat_tui.generate", side_effect=fake_generate):
            app = ChatApp(arguments("/tmp/nonexistent-lchat-config"), "http://localhost:8070/v1/chat/completions")
            async with app.run_test(size=(80, 25)) as pilot:
                await pilot.pause(.2)
                await pilot.press("x", "enter")
                await pilot.pause(.4)
                paragraph = app.query_one("MarkdownParagraph")
                await pilot.mouse_down(paragraph, offset=(0, 0))
                await pilot._post_mouse_events([MouseMove], paragraph, (4, 0), button=1)
                await pilot.mouse_up(paragraph, offset=(4, 0))
                await pilot.pause(.1)
                self.assertEqual(app.screen.get_selected_text().strip(), "Hello")
                with patch.object(app, "copy_to_clipboard") as copy:
                    await pilot.press("ctrl+c")
                    copy.assert_called_once_with("Hello\n")

    async def test_stream_continues_when_text_is_selected(self):
        with patch("lchat_tui.fetch_models_response", return_value=b'{"data":[{"id":"test"}]}'), patch("lchat_tui.generate", return_value=None):
            app = ChatApp(arguments("/tmp/nonexistent-lchat-config"), "http://localhost:8070/v1/chat/completions")
            async with app.run_test(size=(80, 25)) as pilot:
                await pilot.pause(.2)
                await pilot.press("x", "enter")
                app.events.put(("token", "Hello"))
                await pilot.pause(.4)
                paragraph = app.query_one("MarkdownParagraph")
                await pilot.mouse_down(paragraph, offset=(0, 0))
                await pilot._post_mouse_events([MouseMove], paragraph, (4, 0), button=1)
                await pilot.mouse_up(paragraph, offset=(4, 0))
                await pilot.pause(.1)
                app.events.put(("token", " world"))
                await pilot.pause(.3)
                self.assertIn("Hello&#160;world", app.export_screenshot())

    async def test_multiline_enter_and_ctrl_s(self):
        with patch("lchat_tui.fetch_models_response", return_value=b'{"data":[{"id":"test"}]}'), patch("lchat_tui.generate", side_effect=fake_generate):
            args = arguments("/tmp/nonexistent-lchat-config")
            args.enter_sends = False
            app = ChatApp(args, "http://localhost:8070/v1/chat/completions")
            async with app.run_test() as pilot:
                await pilot.pause(.2)
                await pilot.press("a", "enter", "b")
                self.assertEqual(app.query_one(Composer).text, "a\nb")
                await pilot.press("ctrl+s")
                await pilot.pause(.3)
                self.assertEqual(app.conversation[0]["content"], "a\nb")

    async def test_stream_follows_bottom_and_respects_manual_scroll(self):
        with patch("lchat_tui.fetch_models_response", return_value=b'{"data":[{"id":"test"}]}'), patch("lchat_tui.generate", return_value=None):
            app = ChatApp(arguments("/tmp/nonexistent-lchat-config"), "http://localhost:8070/v1/chat/completions")
            async with app.run_test(size=(60, 20)) as pilot:
                await pilot.pause(.2)
                await pilot.press("x", "enter")
                await pilot.pause(.2)
                chat = app.query_one("#chat")
                user = app.query_one(".user")
                self.assertIsNotNone(user.styles.background)
                for number in range(3):
                    app.events.put(("token", "\n\n".join(f"문장 {number}-{i}" for i in range(15)) + "\n\n"))
                    await pilot.pause(.4)
                    self.assertGreater(chat.max_scroll_y, 0)
                    self.assertEqual(chat.scroll_y, chat.max_scroll_y)
                await pilot.press("pageup")
                await pilot.pause(.2)
                self.assertFalse(chat.follow_output)
                app.events.put(("token", "\n\n".join(f"추가 {i}" for i in range(15))))
                await pilot.pause(.4)
                self.assertLess(chat.scroll_y, chat.max_scroll_y)
                for _ in range(12):
                    await pilot.press("pagedown")
                    if chat.is_vertical_scroll_end:
                        break
                await pilot.pause(.2)
                self.assertTrue(chat.follow_output)
                content = app.partial + "\n\n" + "\n\n".join(f"마지막 {i}" for i in range(15))
                app.events.put(("done", {"content": content, "thinking": "", "metrics": {
                    "first_text_seconds": .1, "total_seconds": 1.0, "generation_seconds": .9,
                    "tokens_per_second": 10.0, "exact_tokens": True}}))
                await pilot.pause(.4)
                self.assertEqual(chat.scroll_y, chat.max_scroll_y)

    async def test_rapid_stream_and_mouse_scroll(self):
        with patch("lchat_tui.fetch_models_response", return_value=b'{"data":[{"id":"test"}]}'), patch("lchat_tui.generate", return_value=None):
            app = ChatApp(arguments("/tmp/nonexistent-lchat-config"), "http://localhost:8070/v1/chat/completions")
            async with app.run_test(size=(55, 16)) as pilot:
                await pilot.pause(.2)
                await pilot.press("x", "enter")
                chat = app.query_one("#chat")
                for index in range(15):
                    app.events.put(("token", (f"응답 {index} " * 20) + "\n\n"))
                    await pilot.pause(.11)
                await pilot.pause(.4)
                self.assertTrue(chat.follow_output)
                self.assertGreater(chat.max_scroll_y, 0)
                self.assertEqual(chat.scroll_y, chat.max_scroll_y)
                await pilot._post_mouse_events([MouseScrollUp], chat, (1, 1))
                await pilot.pause(.1)
                self.assertFalse(chat.follow_output)
                app.events.put(("token", "\n\n" + "추가 " * 50))
                await pilot.pause(.3)
                self.assertLess(chat.scroll_y, chat.max_scroll_y)
                # Return to the bottom with the wheel and verify following resumes.
                for _ in range(30):
                    await pilot._post_mouse_events([MouseScrollDown], chat, (1, 1))
                    if chat.is_vertical_scroll_end:
                        break
                await pilot.pause(.3)
                self.assertTrue(chat.follow_output)
                await pilot.resize_terminal(45, 14)
                app.events.put(("token", "\n\n" + "마지막 " * 50))
                await pilot.pause(.4)
                self.assertEqual(chat.scroll_y, chat.max_scroll_y)

    async def test_error_removes_failed_turn(self):
        def fail(args, url, events, payload, started):
            events.put(("error", "test failure"))
        with patch("lchat_tui.fetch_models_response", return_value=b'{"data":[{"id":"test"}]}'), patch("lchat_tui.generate", side_effect=fail):
            app = ChatApp(arguments("/tmp/nonexistent-lchat-config"), "http://localhost:8070/v1/chat/completions")
            async with app.run_test() as pilot:
                await pilot.pause(.2)
                await pilot.press("x", "enter")
                await pilot.pause(.2)
                self.assertEqual(app.conversation, [])
                self.assertFalse(app.busy)

if __name__ == "__main__":
    unittest.main()
