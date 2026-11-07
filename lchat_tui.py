"""Textual full-screen interface for lchat."""
from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, Markdown, Select, Static, TextArea

from lchat import apply_qwen_thinking_setting, completion_url, generate, save_settings, split_thinking_text
from lchat_math import render_math_in_markdown
from lchat_web import fetch_models_response, parse_available_models


class ChatScroll(VerticalScroll):
    """Follow streamed output until the user scrolls away from the bottom."""

    can_focus = False
    can_focus_children = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.follow_output = True

    def follow_latest(self) -> None:
        self.follow_output = True
        self.call_after_refresh(self.scroll_if_following)

    def scroll_if_following(self) -> None:
        if self.follow_output:
            self.scroll_end(animate=False, force=True, immediate=True)

    def resume_if_at_bottom(self) -> None:
        if self.is_vertical_scroll_end:
            self.follow_output = True

    def _on_mouse_scroll_up(self, event: Any) -> None:
        if not event.ctrl and not event.shift and self.max_scroll_y > 0:
            self.follow_output = False
        super()._on_mouse_scroll_up(event)

    def _on_mouse_scroll_down(self, event: Any) -> None:
        super()._on_mouse_scroll_down(event)
        if not event.ctrl and not event.shift:
            self.call_after_refresh(self.resume_if_at_bottom)

    def _on_scroll_to(self, event: Any) -> None:
        if event.y is not None:
            self.follow_output = event.y >= self.max_scroll_y
        super()._on_scroll_to(event)

    def _on_scroll_up(self, event: Any) -> None:
        if self.allow_vertical_scroll:
            self.follow_output = False
            self.scroll_page_up(animate=False)
            event.stop()

    def _on_scroll_down(self, event: Any) -> None:
        if self.allow_vertical_scroll:
            self.scroll_page_down(animate=False)
            self.call_after_refresh(self.resume_if_at_bottom)
            event.stop()


class Composer(TextArea):
    BINDINGS = [Binding("ctrl+s", "send", "전송", priority=True),
                Binding("ctrl+enter", "send", "전송", priority=True),
                Binding("ctrl+u", "clear_input", "입력 지우기", priority=True)]

    def action_send(self) -> None:
        self.app.send_message()

    def action_clear_input(self) -> None:
        self.clear()

    def action_copy(self) -> None:
        if self.screen.get_selected_text():
            self.screen.action_copy_text()
        else:
            super().action_copy()

    async def _on_key(self, event: Any) -> None:
        if event.key == "ctrl+j":
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        if event.key == "enter" and self.app.args.enter_sends:
            event.prevent_default()
            event.stop()
            self.app.send_message()
            return
        if ((event.key == "up" and self.cursor_at_first_line) or
                (event.key == "down" and self.cursor_at_last_line)) and self.app.history:
            event.prevent_default()
            event.stop()
            self.app.navigate_history(-1 if event.key == "up" else 1)
            return
        await super()._on_key(event)


class ModelScreen(ModalScreen):
    CSS = """ModelScreen { align: center middle; } #model-dialog { width: 70%; height: auto; max-height: 80%; border: round $primary; background: $surface; padding: 1 2; }"""
    BINDINGS = [("escape", "dismiss", "취소")]

    def __init__(self, models: list[str], current: str) -> None:
        super().__init__()
        self.models, self.current = models, current

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="model-dialog"):
            yield Label("모델 선택")
            yield Select([(model, model) for model in self.models], value=self.current if self.current in self.models else self.models[0], id="model-select", allow_blank=False)
            with Horizontal():
                yield Button("선택", variant="primary", id="choose")
                yield Button("취소", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(self.query_one("#model-select", Select).value if event.button.id == "choose" else None)


class SettingsScreen(ModalScreen):
    CSS = """
    SettingsScreen { align: center middle; }
    #settings-dialog { width: 92%; max-width: 96; height: auto; max-height: 95%; border: round $primary; background: $surface; padding: 0 1; }
    #settings-title { text-style: bold; color: $text; background: $primary 35%; padding: 0 1; height: 1; }
    .section-title { color: $primary-lighten-2; text-style: bold; height: 1; }
    .setting-pair { height: 1; }
    .setting-row { height: 1; width: 1fr; }
    .setting-row Label { width: 15; height: 1; color: $text-muted; }
    .setting-row Input { width: 1fr; height: 1; border: none; padding: 0 1; background: $boost; }
    .setting-row Input:focus { border: none; background: $primary 35%; }
    #settings-dialog.narrow .setting-pair { layout: vertical; height: 2; }
    #settings-dialog.narrow .setting-pair .setting-row { width: 100%; }
    #system { height: 3; border: round $primary 50%; }
    #system:focus { border: round $primary; }
    Checkbox { height: 1; border: none; padding: 0 1; }
    Checkbox:focus { border: none; }
    #settings-error { display: none; height: auto; color: $error; }
    #settings-buttons { height: 3; align-horizontal: right; }
    #settings-buttons Button { min-width: 10; }
    """
    BINDINGS = [("escape", "dismiss", "취소")]

    def __init__(self, args: Any) -> None:
        super().__init__()
        self.args = args
        self.saving = False

    def action_dismiss(self) -> None:
        if not self.saving:
            self.dismiss(None)

    def compose(self) -> ComposeResult:
        a = self.args
        def field(key: str, label: str) -> ComposeResult:
            value = getattr(a, key, None)
            with Horizontal(classes="setting-row"):
                yield Label(label)
                yield Input(value="" if value is None else str(value), password=key == "api_key", id=key.replace("_", "-"))

        with VerticalScroll(id="settings-dialog"):
            yield Static("환경 설정  ·  Esc 취소", id="settings-title")
            yield Static("── 연결", classes="section-title")
            yield from field("endpoint", "Endpoint")
            yield from field("model", "Model ID")
            yield from field("api_key", "API key")
            yield Static("── 응답", classes="section-title")
            with Horizontal(classes="setting-pair"):
                yield from field("timeout", "Timeout (초)")
                yield from field("temperature", "Temperature")
            yield from field("max_tokens", "Max tokens")
            yield Static("── 웹 호스팅 · 포트가 비어 있으면 꺼짐", classes="section-title")
            with Horizontal(classes="setting-pair"):
                yield from field("host", "Bind host")
                yield from field("port", "Port")
            yield Static("── System prompt", classes="section-title")
            yield TextArea(a.system, id="system")
            yield Checkbox("Enter로 전송 (해제 시 Ctrl+S)", value=a.enter_sends, id="enter-sends")
            yield Checkbox("TUI 실행", value=a.tui_enabled, id="tui-enabled")
            yield Checkbox("Qwen 추론 끄기", value=a.disable_qwen_thinking, id="disable-qwen-thinking")
            yield Static("", id="settings-error")
            with Horizontal(id="settings-buttons"):
                yield Button("취소", id="cancel")
                yield Button("저장", variant="primary", id="save")

    def on_resize(self) -> None:
        self.query_one("#settings-dialog").set_class(self.size.width < 76, "narrow")

    def show_message(self, text: str) -> None:
        message = self.query_one("#settings-error", Static)
        message.update(text)
        message.styles.display = "block" if text else "none"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_dismiss()
            return
        try:
            def value(name: str) -> str:
                return self.query_one("#" + name.replace("_", "-"), Input).value.strip()
            data = {key: value(key) for key in ("endpoint", "model", "api_key", "host")}
            completion_url(data["endpoint"])
            if not data["model"]:
                raise ValueError("모델 ID를 입력하세요.")
            data["timeout"] = float(value("timeout"))
            data["temperature"] = float(value("temperature")) if value("temperature") else None
            data["max_tokens"] = int(value("max_tokens")) if value("max_tokens") else None
            data["port"] = int(value("port")) if value("port") else None
            if not 1 <= data["timeout"] <= 1800 or (data["temperature"] is not None and not 0 <= data["temperature"] <= 2) or (data["max_tokens"] is not None and data["max_tokens"] < 1) or (data["port"] is not None and not 1 <= data["port"] <= 65535):
                raise ValueError("Timeout, temperature, max tokens 또는 port 범위를 확인하세요.")
            data["host"] = data["host"] or "127.0.0.1"
            data["system"] = self.query_one("#system", TextArea).text
            for key in ("enter_sends", "tui_enabled", "disable_qwen_thinking"):
                data[key] = self.query_one("#" + key.replace("_", "-"), Checkbox).value
        except ValueError as error:
            self.show_message(str(error))
            return
        self.saving = True
        event.button.disabled = True
        self.query_one("#cancel", Button).disabled = True
        self.show_message("서버 확인 중…")
        self.run_worker(lambda: self._validate(data), thread=True)

    def _validate(self, data: dict[str, Any]) -> None:
        try:
            models = parse_available_models(fetch_models_response(data))
            if data["model"] not in models:
                raise ValueError("서버 모델 목록에 지정한 모델이 없습니다.")
            path = Path(self.args.config_path)
            saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            saved.update(data)
            save_settings(path, saved)
        except Exception as error:
            self.app.call_from_thread(self._save_error, str(error))
        else:
            self.app.call_from_thread(self._save_done, data)

    def _save_done(self, data: dict[str, Any]) -> None:
        if self.is_mounted:
            self.dismiss(data)

    def _save_error(self, message: str) -> None:
        if not self.is_mounted:
            return
        self.saving = False
        self.show_message("오류: " + message)
        self.query_one("#save", Button).disabled = False
        self.query_one("#cancel", Button).disabled = False


class ChatApp(App):
    TITLE = "lchat"
    CSS = """#chat { height: 1fr; padding: 0 1; } .user-row { height: auto; align-horizontal: right; margin-top: 1; } .user { width: auto; max-width: 80%; padding: 0 1; background: $primary 30%; color: $text; } .assistant { margin-top: 1; } #status { height: auto; color: $accent; } #composer { height: 5; border: round $primary; }"""
    BINDINGS = [Binding("ctrl+q", "quit", "종료", priority=True), Binding("ctrl+d", "quit", "종료", priority=True, show=False), Binding("f2", "settings", "설정"), Binding("f3", "thinking", "생각"), Binding("f4", "models", "모델"), Binding("ctrl+l", "redraw", "새로고침"), Binding("pageup", "scroll_up", "위로", show=False, priority=True), Binding("pagedown", "scroll_down", "아래로", show=False, priority=True)]

    def __init__(self, args: Any, url: str) -> None:
        super().__init__()
        self.args, self.url = args, url
        self.conversation: list[dict[str, Any]] = []
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.show_thinking = False
        self.partial = self.reasoning = ""
        self.current = None
        self.history: list[str] = []
        self.history_index = 0
        self.history_draft = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield ChatScroll(id="chat")
        yield Static("F2 설정 · F3 생각 · F4 모델", id="status")
        yield Composer(id="composer")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(Composer).focus()
        self.set_interval(0.1, self.poll_events)
        self.action_models(startup=True)

    def status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    def action_redraw(self) -> None:
        self.refresh(repaint=True, layout=True)

    def action_scroll_up(self) -> None:
        chat = self.query_one(ChatScroll)
        if chat.max_scroll_y > 0:
            chat.follow_output = False
        chat.scroll_page_up(animate=False)

    def action_scroll_down(self) -> None:
        chat = self.query_one(ChatScroll)
        chat.scroll_page_down(animate=False)
        chat.call_after_refresh(chat.resume_if_at_bottom)

    def action_settings(self) -> None:
        if not self.busy:
            self.push_screen(SettingsScreen(self.args), self.settings_done)

    def settings_done(self, data: dict[str, Any] | None) -> None:
        if data:
            for key, value in data.items():
                setattr(self.args, key, value)
            self.url = completion_url(self.args.endpoint)
            self.status("설정 저장됨 · 웹 바인딩과 실행 모드는 재시작 후 적용")

    def action_models(self, startup: bool = False) -> None:
        if self.busy:
            return
        self.status("모델 목록 확인 중…")
        config = {key: getattr(self.args, key) for key in ("endpoint", "model", "api_key", "timeout")}
        def fetch() -> None:
            try:
                models = parse_available_models(fetch_models_response(config))
                self.call_from_thread(self.models_loaded, models, startup)
            except Exception as error:
                self.call_from_thread(self.status, "모델 목록 오류: " + str(error))
        self.run_worker(fetch, thread=True)

    def models_loaded(self, models: list[str], startup: bool) -> None:
        if not models:
            self.status("서버에 모델이 없습니다. F2에서 설정을 확인하세요.")
        elif startup and self.args.model in models:
            self.status("모델: " + self.args.model)
        else:
            self.push_screen(ModelScreen(models, self.args.model), self.model_selected)

    def model_selected(self, model: str | None) -> None:
        if model:
            self.args.model = model
            self.status("모델: " + model + " · 영구 저장하려면 F2")

    async def action_thinking(self) -> None:
        self.show_thinking = not self.show_thinking
        if self.current:
            await self.update_current()
        elif self.conversation and self.conversation[-1]["role"] == "assistant":
            item = self.conversation[-1]
            if item.get("widget"):
                await self.render_response(item["widget"], item["content"], item.get("thinking", ""), True)

    async def render_response(self, widget: Markdown, content: str, reasoning: str, final: bool) -> None:
        answer, tagged = split_thinking_text(content, final=final)
        answer = render_math_in_markdown(answer)
        thought = reasoning + tagged
        text = ("**생각**\n\n> " + thought.replace("\n", "\n> ") + "\n\n" if self.show_thinking and thought else "") + answer
        chat = self.query_one(ChatScroll)
        await widget.update(text or ("_생각 중…_" if thought else "_응답 대기 중…_"))
        if chat.follow_output:
            chat.call_after_refresh(chat.scroll_if_following)

    async def update_current(self) -> None:
        if self.current:
            await self.render_response(self.current, self.partial, self.reasoning, False)

    def navigate_history(self, direction: int) -> None:
        composer = self.query_one(Composer)
        if self.history_index == len(self.history):
            self.history_draft = composer.text
        self.history_index = max(0, min(len(self.history), self.history_index + direction))
        composer.load_text(self.history[self.history_index] if self.history_index < len(self.history) else self.history_draft)
        composer.move_cursor(composer.document.end)

    def send_message(self) -> None:
        if self.busy:
            self.status("응답이 끝난 뒤 전송하세요.")
            return
        composer = self.query_one(Composer)
        text = composer.text.strip()
        if not text:
            return
        composer.clear()
        if text.lower() in ("/exit", "/quit"):
            self.exit()
            return
        if text.lower() == "/reset":
            self.conversation.clear()
            self.query_one("#chat").remove_children()
            self.status("대화 기록을 지웠습니다.")
            return
        if text.lower() == "/help":
            self.status("/help · /reset · /exit | F2 설정 · F3 생각 · F4 모델 | Ctrl+S 전송")
            return
        if not self.args.model:
            self.status("먼저 F4에서 모델을 선택하세요.")
            composer.text = text
            return
        if not self.history or self.history[-1] != text:
            self.history.append(text)
        self.history_index = len(self.history)
        self.history_draft = ""
        chat = self.query_one(ChatScroll)
        chat.mount(Horizontal(Static(text, classes="user", markup=False), classes="user-row"))
        self.conversation.append({"role": "user", "content": text})
        self.current = Markdown("_응답 대기 중…_", classes="assistant")
        chat.mount(self.current)
        chat.follow_latest()
        messages = [{"role": "system", "content": self.args.system}] + [{"role": x["role"], "content": x["content"]} for x in self.conversation]
        payload = {"model": self.args.model, "messages": messages, "stream": not self.args.no_stream}
        if self.args.temperature is not None:
            payload["temperature"] = self.args.temperature
        if self.args.max_tokens is not None:
            payload["max_tokens"] = self.args.max_tokens
        apply_qwen_thinking_setting(payload, self.args.model, self.args.disable_qwen_thinking)
        self.partial = self.reasoning = ""
        self.busy = True
        self.status("연결 중…")
        threading.Thread(target=generate, args=(self.args, self.url, self.events, payload, time.monotonic()), daemon=True).start()

    async def poll_events(self) -> None:
        changed = False
        while True:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "token":
                self.partial += data
                changed = True
            elif kind == "thinking":
                self.reasoning += data
                changed = True
                self.status("추론 중…")
            elif kind == "connected":
                self.status("응답 대기 중…")
            elif kind == "first_text":
                self.status("응답 생성 중…")
            elif kind == "done":
                self.busy = False
                await self.render_response(self.current, data["content"], data["thinking"], True)
                self.conversation.append({"role": "assistant", "content": data["content"], "thinking": data["thinking"], "widget": self.current})
                metrics = data["metrics"]
                rate = metrics["tokens_per_second"]
                self.status(f"완료 · 첫 출력 {metrics['first_text_seconds'] or 0:.2f}초 · 생성 {metrics['generation_seconds']:.2f}초 · {'~' if not metrics['exact_tokens'] else ''}{rate:.1f} tok/s · 전체 {metrics['total_seconds']:.2f}초" if rate is not None else f"완료 · 전체 {metrics['total_seconds']:.2f}초")
                self.current = None
                changed = False
            elif kind == "error":
                self.busy = False
                self.conversation.pop()
                if self.current:
                    self.current.remove()
                    self.current = None
                self.status("API 오류: " + str(data))
                changed = False
        if changed:
            await self.update_current()
