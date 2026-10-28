#!/usr/bin/env python3
"""A small terminal chatbot for OpenAI-compatible chat completion APIs."""

from __future__ import annotations

import argparse
import json
import locale
import math
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    import readline
except ImportError:
    readline = None


DEFAULT_CONFIG_PATH = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "lchat" / "config.json"
DEFAULT_SETTINGS: dict[str, Any] = {
    "endpoint": "http://localhost:8070/v1",
    "model": "",
    "api_key": "",
    "system": "You are a helpful assistant.",
    "enter_sends": True,
    "tui_enabled": True,
    "host": "127.0.0.1",
    "port": None,
    "timeout": 180,
    "temperature": None,
    "max_tokens": None,
}


def save_settings(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, temp_name = tempfile.mkstemp(prefix=".config-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(settings, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Chat in your terminal with an OpenAI-compatible API."
    )
    parser.add_argument(
        "--endpoint",
        help="API base URL (or full /chat/completions URL); env: OPENAI_BASE_URL",
    )
    parser.add_argument("--model", help="Model name; env: OPENAI_MODEL")
    parser.add_argument(
        "--api-key",
        help="API key (optional for local servers); env: OPENAI_API_KEY",
    )
    parser.add_argument("--system", help="System prompt; env: LCHAT_SYSTEM_PROMPT")
    parser.add_argument("--host", help="Web listen address; env: LCHAT_HOST")
    parser.add_argument("--port", type=int, help="Also serve the web UI on this port; env: LCHAT_PORT")
    parser.add_argument("--timeout", type=float, help="Request timeout in seconds")
    parser.add_argument("--temperature", type=float, help="Sampling temperature (API default if omitted)")
    parser.add_argument("--max-tokens", type=int, help="Maximum generated tokens")
    parser.add_argument("--config", help=f"JSON settings file (default: {DEFAULT_CONFIG_PATH})")
    parser.add_argument("--cfg", action="store_true", help="Print the absolute settings-file path and exit")
    parser.add_argument("--reset", action="store_true", help="Reset settings to defaults before running")
    parser.add_argument("--no-stream", action="store_true", help="Wait for the complete answer before displaying it")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--tui", action="store_true", help="Force the full-screen ncurses terminal UI")
    mode.add_argument("--plain", action="store_true", help="Use the simple line-oriented terminal UI (without web hosting)")
    mode.add_argument("--no-tui", action="store_true", help="Disable TUI (web-only when web hosting is enabled)")
    parser.add_argument("--terminal-only", action="store_true", help=argparse.SUPPRESS)
    return parser


def terminal_cell_width(text: str) -> int:
    width = 0
    for char in text:
        category = unicodedata.category(char)
        if unicodedata.combining(char) or category in ("Cc", "Cf", "Mn", "Me"):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def wrap_for_terminal(text: str, max_width: int) -> list[str]:
    lines: list[str] = []
    paragraphs = text.replace("\\t", "    ").splitlines() or [""]
    for paragraph in paragraphs:
        line = ""
        for word in paragraph.split():
            chunks: list[str] = []
            chunk = ""
            for char in word:
                if chunk and terminal_cell_width(chunk + char) > max_width:
                    chunks.append(chunk)
                    chunk = ""
                chunk += char
            if chunk or not chunks:
                chunks.append(chunk)
            for index, part in enumerate(chunks):
                separator = " " if line and index == 0 else ""
                if line and terminal_cell_width(line + separator + part) > max_width:
                    lines.append(line)
                    line = part
                else:
                    line += separator + part
                if index < len(chunks) - 1:
                    lines.append(line)
                    line = ""
        lines.append(line)
    return lines or [""]


def layout_input_text(text: str, max_width: int) -> list[tuple[str, int, int, bool]]:
    """Return wrapped input rows as (text, start index, end index, soft-wrapped)."""
    max_width = max(1, max_width)
    rows: list[tuple[str, int, int, bool]] = []
    line: list[str] = []
    start = 0
    width = 0
    for index, char in enumerate(text):
        if char == "\n":
            rows.append(("".join(line), start, index, False))
            line = []
            start = index + 1
            width = 0
            continue
        char_width = terminal_cell_width(char)
        if line and width + char_width > max_width:
            rows.append(("".join(line), start, index, True))
            line = []
            start = index
            width = 0
        line.append(char)
        width += char_width
    rows.append(("".join(line), start, len(text), False))
    return rows


def input_cursor_position(rows: list[tuple[str, int, int, bool]], cursor: int) -> tuple[int, int]:
    for row_index, (line, start, end, soft_wrap) in enumerate(rows):
        if start <= cursor < end:
            return row_index, terminal_cell_width(line[:cursor - start])
        if cursor == end:
            if soft_wrap and row_index + 1 < len(rows):
                return row_index + 1, 0
            return row_index, terminal_cell_width(line)
    last = len(rows) - 1
    return last, terminal_cell_width(rows[last][0])


def input_index_at_column(rows: list[tuple[str, int, int, bool]], row_index: int, column: int) -> int:
    line, start, end, soft_wrap = rows[row_index]
    position = start
    used = 0
    for offset, char in enumerate(line):
        char_width = terminal_cell_width(char)
        if used + char_width > column:
            break
        used += char_width
        position = start + offset + 1
    if soft_wrap and position == end and end > start:
        position = end - 1
    return position


def split_thinking_text(text: str, final: bool = False) -> tuple[str, str]:
    open_tag, close_tag = "<thinking>", "</thinking>"
    lowered = text.lower()
    visible: list[str] = []
    thinking: list[str] = []
    position = 0
    inside = False
    while position < len(text):
        if inside:
            end = lowered.find(close_tag, position)
            if end < 0:
                thinking.append(text[position:])
                break
            thinking.append(text[position:end])
            position = end + len(close_tag)
            inside = False
        else:
            start = lowered.find(open_tag, position)
            if start < 0:
                tail = text[position:]
                if not final:
                    pending = max((n for n in range(1, min(len(open_tag), len(tail) + 1)) if tail[-n:].lower() == open_tag[:n]), default=0)
                    visible.append(tail[:-pending] if pending else tail)
                else:
                    visible.append(tail)
                break
            visible.append(text[position:start])
            position = start + len(open_tag)
            inside = True
    return "".join(visible), "".join(thinking)


def user_bubble_lines(text: str, columns: int) -> list[str]:
    columns = max(12, columns)
    max_inner = max(1, min(columns - 6, int(columns * 0.68)))
    wrapped = wrap_for_terminal(text, max_inner)
    inner_width = max(1, *(terminal_cell_width(line) for line in wrapped))
    box_width = inner_width + 4
    left_pad = max(0, columns - box_width - 1)
    prefix = " " * left_pad
    top = prefix + "┌" + "─" * (inner_width + 2) + "┐"
    body = [
        prefix + "│ " + line + " " * (inner_width - terminal_cell_width(line)) + " │"
        for line in wrapped
    ]
    bottom = prefix + "└" + "─" * (inner_width + 2) + "┘"
    return [top, *body, bottom]


def print_user_bubble(text: str) -> None:
    columns = shutil.get_terminal_size(fallback=(80, 24)).columns
    if sys.stdin.isatty() and sys.stdout.isatty():
        echoed_width = terminal_cell_width("you> " + text)
        rows = max(1, math.ceil(echoed_width / max(1, columns)))
        print(f"\x1b[{rows}A\r", end="")
        for index in range(rows):
            print("\x1b[2K", end="")
            if index < rows - 1:
                print("\x1b[1B\r", end="")
        if rows > 1:
            print(f"\x1b[{rows - 1}A\r", end="")
    else:
        print()
    for line in user_bubble_lines(text, columns):
        print(line)


def completion_url(endpoint: str) -> str:
    endpoint = endpoint.strip().rstrip("/")
    if not endpoint:
        raise ValueError("Endpoint cannot be empty.")
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Endpoint must be an http:// or https:// URL.")
    if parsed.path.rstrip("/").endswith("/chat/completions"):
        return endpoint
    return endpoint + "/chat/completions"


def make_request(url: str, payload: dict[str, Any], api_key: str) -> urllib.request.Request:
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream, application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )


def error_message(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read().decode("utf-8", errors="replace").strip()
    except Exception:
        body = ""
    return f"HTTP {error.code} {error.reason}" + (f":\n{body}" if body else "")


def stream_answer(response: Any) -> str:
    pieces: list[str] = []
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = event.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str) and content:
            print(content, end="", flush=True)
            pieces.append(content)
        elif isinstance(content, list):
            # Support APIs that return text as content blocks.
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    text = block["text"]
                    print(text, end="", flush=True)
                    pieces.append(text)
    print()
    return "".join(pieces)


def complete(url: str, payload: dict[str, Any], api_key: str, timeout: float) -> str:
    request = make_request(url, payload, api_key)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if payload.get("stream"):
                return stream_answer(response)
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise RuntimeError(error_message(error)) from None
    except urllib.error.URLError as error:
        raise RuntimeError(f"Could not connect: {error.reason}") from None
    except TimeoutError:
        raise RuntimeError(f"Request timed out after {timeout:g} seconds.") from None
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"The server returned invalid JSON: {error}") from None

    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError(f"The server response did not contain any choices: {result}")
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        )
    if not isinstance(content, str):
        raise RuntimeError("The server returned an unsupported message format.")
    print(content)
    return content


def run_tui(args: argparse.Namespace, url: str) -> int:
    import curses

    events: queue.Queue[tuple[str, Any]] = queue.Queue()
    conversation: list[dict[str, Any]] = []

    def generate(payload: dict[str, Any], request_started_at: float) -> None:
        pieces: list[str] = []
        reasoning_parts: list[str] = []
        all_text_parts: list[str] = []
        first_text_seconds: float | None = None
        completion_tokens: int | None = None

        def text_parts(value: Any) -> list[str]:
            if isinstance(value, str):
                return [value] if value else []
            if isinstance(value, list):
                return [part for item in value for part in text_parts(item)]
            if isinstance(value, dict):
                text_fields = ("text", "content", "arguments", "reasoning_content", "reasoning", "thinking", "analysis", "refusal", "tool_calls", "function", "name")
                return [part for key in text_fields if key in value for part in text_parts(value[key])]
            return []

        def note_first_text(parts: list[str]) -> None:
            nonlocal first_text_seconds
            if parts and first_text_seconds is None:
                first_text_seconds = max(0.0, time.monotonic() - request_started_at)
                events.put(("first_text", first_text_seconds))

        try:
            request_payload = payload.copy()
            if request_payload.get("stream"):
                request_payload["stream_options"] = {"include_usage": True}
            request = make_request(url, request_payload, args.api_key)
            try:
                response_context = urllib.request.urlopen(request, timeout=args.timeout)
            except urllib.error.HTTPError as error:
                if request_payload.get("stream_options") and error.code in (400, 422):
                    error.close()
                    request_payload.pop("stream_options", None)
                    request = make_request(url, request_payload, args.api_key)
                    response_context = urllib.request.urlopen(request, timeout=args.timeout)
                else:
                    raise
            with response_context as response:
                events.put(("connected", None))
                if payload["stream"]:
                    for raw_line in response:
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            event = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        usage = event.get("usage")
                        if isinstance(usage, dict) and isinstance(usage.get("completion_tokens"), int):
                            completion_tokens = usage["completion_tokens"]
                        choices = event.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        display_parts = text_parts(delta.get("content"))
                        all_parts: list[str] = []
                        for field in ("content", "reasoning_content", "reasoning", "thinking", "analysis", "refusal", "tool_calls"):
                            all_parts.extend(text_parts(delta.get(field)))
                        reasoning_text = [part for field in ("reasoning_content", "reasoning", "thinking", "analysis") for part in text_parts(delta.get(field))]
                        reasoning_parts.extend(reasoning_text)
                        for thought in reasoning_text:
                            events.put(("thinking", thought))
                        note_first_text(all_parts)
                        all_text_parts.extend(all_parts)
                        for text in display_parts:
                            pieces.append(text)
                            events.put(("token", text))
                else:
                    result = json.loads(response.read().decode("utf-8"))
                    usage = result.get("usage")
                    if isinstance(usage, dict) and isinstance(usage.get("completion_tokens"), int):
                        completion_tokens = usage["completion_tokens"]
                    choices = result.get("choices") or []
                    message = choices[0].get("message") or {} if choices else {}
                    display_parts = text_parts(message.get("content"))
                    all_parts: list[str] = []
                    for field in ("content", "reasoning_content", "reasoning", "thinking", "analysis", "refusal", "tool_calls"):
                        all_parts.extend(text_parts(message.get(field)))
                    reasoning_text = [part for field in ("reasoning_content", "reasoning", "thinking", "analysis") for part in text_parts(message.get(field))]
                    reasoning_parts.extend(reasoning_text)
                    for thought in reasoning_text:
                        events.put(("thinking", thought))
                    note_first_text(all_parts)
                    all_text_parts.extend(all_parts)
                    for text in display_parts:
                        pieces.append(text)
                        events.put(("token", text))
            completed_at = time.monotonic()
            total_seconds = max(0.0, completed_at - request_started_at)
            generation_seconds = max(0.0, total_seconds - first_text_seconds) if first_text_seconds is not None else 0.0
            exact_tokens = completion_tokens is not None
            generated_text = "".join(all_text_parts)
            estimated_tokens = max(1, round(len(generated_text.encode("utf-8")) / 4)) if generated_text else 0
            tokens = completion_tokens if exact_tokens else estimated_tokens
            rate = tokens / generation_seconds if generation_seconds >= 0.01 and tokens else None
            metrics = {
                "first_text_seconds": first_text_seconds,
                "total_seconds": total_seconds,
                "generation_seconds": generation_seconds,
                "tokens": tokens,
                "tokens_per_second": rate,
                "exact_tokens": exact_tokens,
                "streaming": bool(payload.get("stream")),
            }
            events.put(("done", {"content": "".join(pieces), "thinking": "".join(reasoning_parts), "metrics": metrics}))
        except urllib.error.HTTPError as error:
            detail = error_message(error)
            events.put(("error", detail))
            if error.code == 404 or (error.code in (400, 422) and "model" in detail.lower()):
                events.put(("recheck_models", None))
        except urllib.error.URLError as error:
            events.put(("error", f"Could not connect: {getattr(error, 'reason', error)}"))
        except (OSError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as error:
            events.put(("error", str(error)))

    def tui(screen: Any) -> int:
        nonlocal url
        try:
            curses.raw()
            curses.nonl()
            curses.curs_set(1)
        except curses.error:
            pass
        screen.keypad(True)
        screen.timeout(80)
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        except curses.error:
            pass
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            curses.init_pair(3, curses.COLOR_RED, -1)
        except curses.error:
            pass

        input_text = ""
        cursor = 0
        prompt_history: list[str] = []
        history_index = 0
        history_draft = ""
        vertical_goal_x: int | None = None
        scroll_from_bottom = 0
        input_hint = "Enter 전송 · Ctrl+J 줄바꿈" if args.enter_sends else "Enter 줄바꿈 · Ctrl+Enter 전송 (Ctrl+S 대체)"
        web_host = "localhost" if args.host in ("0.0.0.0", "::") else (args.host or "localhost")
        web_hint = f" · Web UI http://{web_host}:{args.port}/" if args.port is not None else ""
        status = ("설정 버튼/F2에서 모델을 선택하세요." if not args.model else f"{input_hint} · ↑/↓ 이동·경계에서 기록 · PgUp/PgDn 스크롤 · F2 설정") + web_hint
        busy = False
        streaming_raw = ""
        streaming_text = ""
        streaming_thinking = ""
        server_connected = False
        first_text_seconds: float | None = None
        body_height = 1
        settings_x = 0
        thinking_toggle_rows: dict[int, int] = {}

        def recall_history(direction: int) -> None:
            nonlocal input_text, cursor, history_index, history_draft, status, vertical_goal_x
            vertical_goal_x = None
            if not prompt_history:
                status = "입력 기록이 없습니다."
                return
            if direction < 0:
                if history_index >= len(prompt_history):
                    history_draft = input_text
                    history_index = len(prompt_history)
                if history_index > 0:
                    history_index -= 1
                    input_text = prompt_history[history_index]
                    cursor = len(input_text)
            elif history_index < len(prompt_history) - 1:
                history_index += 1
                input_text = prompt_history[history_index]
                cursor = len(input_text)
            elif history_index == len(prompt_history) - 1:
                history_index = len(prompt_history)
                input_text = history_draft
                cursor = len(input_text)

        def toggle_latest_thinking() -> None:
            nonlocal scroll_from_bottom, status
            for message in reversed(conversation):
                if message["role"] == "assistant" and message.get("thinking"):
                    message["thinking_expanded"] = not message.get("thinking_expanded", False)
                    scroll_from_bottom = 0
                    status = "생각을 펼쳤습니다. F3/표시 줄 클릭으로 접을 수 있습니다." if message["thinking_expanded"] else "생각을 접었습니다."
                    return
            status = "펼칠 생각 기록이 없습니다."

        def move_vertical(direction: int, input_rows: list[tuple[str, int, int, bool]]) -> None:
            nonlocal cursor, vertical_goal_x
            visual_row, visual_column = input_cursor_position(input_rows, cursor)
            target_row = visual_row + direction
            if 0 <= target_row < len(input_rows):
                if vertical_goal_x is None:
                    vertical_goal_x = visual_column
                cursor = input_index_at_column(input_rows, target_row, vertical_goal_x)
            else:
                recall_history(direction)

        def addstr(y: int, x: int, text: str, attr: int = 0, limit: int | None = None) -> None:
            rows, cols = screen.getmaxyx()
            if y < 0 or y >= rows or x < 0 or x >= cols:
                return
            clipped: list[str] = []
            used = 0
            available = cols - x - 1 if limit is None else min(cols - x - 1, limit)
            for char in text:
                width = terminal_cell_width(char)
                if used + width > available:
                    break
                clipped.append(char)
                used += width
            try:
                screen.addstr(y, x, "".join(clipped), attr)
            except curses.error:
                pass

        def choose_model(available: list[str], current: str) -> str | None:
            if not available:
                return None
            selected = available.index(current) if current in available else 0
            first = 0
            while True:
                rows, columns = screen.getmaxyx()
                if rows < 10 or columns < 30:
                    return None
                height = min(rows - 2, max(8, min(len(available) + 5, 20)))
                width = min(columns - 2, 84)
                top, left = (rows - height) // 2, (columns - width) // 2
                win = curses.newwin(height, width, top, left)
                win.keypad(True)
                win.timeout(-1)
                visible = height - 4
                first = max(0, min(first, max(0, len(available) - visible)))
                if selected < first:
                    first = selected
                elif selected >= first + visible:
                    first = selected - visible + 1
                win.box()
                def label(row: int, text: str, attr: int = 0) -> None:
                    clipped = ""
                    for char in text:
                        if terminal_cell_width(clipped + char) > width - 4:
                            break
                        clipped += char
                    try:
                        win.addstr(row, 2, clipped, attr)
                    except curses.error:
                        pass
                label(1, "모델 선택 · 서버에서 제공하는 모델", curses.A_BOLD | curses.color_pair(1))
                for row, index in enumerate(range(first, min(len(available), first + visible)), 2):
                    label(row, ("▶ " if index == selected else "  ") + available[index], curses.A_REVERSE if index == selected else 0)
                label(height - 2, f"↑/↓ 선택 · Enter 확인 · Esc 취소  ({selected + 1}/{len(available)})")
                win.refresh()
                try:
                    key = win.get_wch()
                except curses.error:
                    continue
                if key in ("\x1b", "\x03", "\x04"):
                    return None
                if key in ("\n", "\r", curses.KEY_ENTER):
                    return available[selected]
                if key == curses.KEY_UP:
                    selected = max(0, selected - 1)
                elif key == curses.KEY_DOWN:
                    selected = min(len(available) - 1, selected + 1)
                elif key == curses.KEY_PPAGE:
                    selected = max(0, selected - visible)
                elif key == curses.KEY_NPAGE:
                    selected = min(len(available) - 1, selected + visible)
                elif key == curses.KEY_RESIZE:
                    continue
                elif key == curses.KEY_MOUSE:
                    try:
                        _, mx, my, _, buttons = curses.getmouse()
                        index = first + my - top - 2
                        if left <= mx < left + width and 0 <= index < len(available) and 2 <= my - top < 2 + visible and buttons & (getattr(curses, "BUTTON1_CLICKED", 0) | getattr(curses, "BUTTON1_PRESSED", 0)):
                            return available[index]
                    except curses.error:
                        pass

        def edit_settings() -> dict[str, Any] | None:
            nonlocal status
            rows, columns = screen.getmaxyx()
            if rows < 16 or columns < 50:
                status = "설정 창을 열려면 터미널을 50x16 이상으로 늘려 주세요."
                return None
            height = min(rows - 2, 22)
            width = min(columns - 4, 96)
            top = max(1, (rows - height) // 2)
            left = max(1, (columns - width) // 2)
            win = curses.newwin(height, width, top, left)
            win.keypad(True)
            win.timeout(-1)
            fields = [
                ("endpoint", "Endpoint", args.endpoint),
                ("model", "Model ID", args.model),
                ("api_key", "API key", ""),
                ("system", "System prompt", args.system),
                ("enter_sends", "Enter 동작", bool(args.enter_sends)),
                ("tui_enabled", "TUI 실행", bool(args.tui_enabled)),
                ("timeout", "Timeout seconds", str(args.timeout)),
                ("temperature", "Temperature", "" if args.temperature is None else str(args.temperature)),
                ("max_tokens", "Max tokens", "" if args.max_tokens is None else str(args.max_tokens)),
                ("web_enabled", "Web UI 실행", args.port is not None),
                ("host", "Web bind host", args.host or "127.0.0.1"),
                ("port", "Web port", str(args.port or 8078)),
            ]
            values = {key: value for key, _, value in fields}
            labels = {key: label for key, label, _ in fields}
            clear_api_key = False
            focus = 0
            field_count = len(fields)
            save_focus, cancel_focus = field_count, field_count + 1
            message = "Tab/↑↓ 이동 · Space 전환 · Enter 저장/선택 · Esc 취소 · Ctrl+U 지우기"
            button_y = height - 4
            status_y = height - 3
            hint_y = height - 2
            save_text, cancel_text = "[ 저장 ]", "[ 취소 ]"
            save_x = max(2, (width - terminal_cell_width(save_text) - terminal_cell_width(cancel_text) - 5) // 2)
            cancel_x = save_x + terminal_cell_width(save_text) + 4
            field_rows: dict[int, int] = {}

            def draw_modal_text(y: int, text: str, attr: int = 0) -> None:
                try:
                    win.addstr(y, 2, " " * (width - 4))
                    clipped = []
                    used = 0
                    for char in text:
                        char_width = terminal_cell_width(char)
                        if used + char_width > width - 5:
                            break
                        clipped.append(char)
                        used += char_width
                    win.addstr(y, 2, "".join(clipped), attr)
                except curses.error:
                    pass

            def save_form() -> dict[str, Any] | None:
                nonlocal message, clear_api_key
                endpoint = str(values["endpoint"]).strip()
                model = str(values["model"]).strip()
                if not endpoint:
                    message = "오류: Endpoint를 입력하세요."
                    return None
                try:
                    timeout = float(values["timeout"])
                    temperature = None if str(values["temperature"]).strip() == "" else float(values["temperature"])
                    max_tokens = None if str(values["max_tokens"]).strip() == "" else int(values["max_tokens"])
                    web_port = int(values["port"]) if values["web_enabled"] else None
                except (TypeError, ValueError):
                    message = "오류: Timeout/Temperature/Max tokens/Port 숫자를 확인하세요."
                    return None
                if not 1 <= timeout <= 1800:
                    message = "오류: Timeout은 1~1800초여야 합니다."
                    return None
                if temperature is not None and not 0 <= temperature <= 2:
                    message = "오류: Temperature는 0~2 범위여야 합니다."
                    return None
                if max_tokens is not None and max_tokens < 1:
                    message = "오류: Max tokens는 1 이상이어야 합니다."
                    return None
                if web_port is not None and not 1 <= web_port <= 65535:
                    message = "오류: Web port는 1~65535 범위여야 합니다."
                    return None
                host = str(values["host"]).strip() or "127.0.0.1"
                key = "" if clear_api_key else (str(values["api_key"]) or str(args.api_key or ""))
                message = "Endpoint와 모델 확인 중…"
                draw_modal_text(status_y, message)
                win.refresh()
                try:
                    completion_url(endpoint)
                    from webchat import fetch_models_response, parse_available_models
                    check_config = {"endpoint": endpoint, "model": model, "api_key": key, "timeout": timeout}
                    available = parse_available_models(fetch_models_response(check_config))
                    if model not in available:
                        if not available:
                            message = "오류: 서버에 사용 가능한 모델이 없습니다."
                            return None
                        chosen = choose_model(available, model)
                        win.touchwin()
                        if chosen is None:
                            message = "모델 선택을 취소했습니다."
                            return None
                        model = chosen
                        values["model"] = model
                    path = Path(args.config_path)
                    saved: dict[str, Any] = {}
                    if path.exists():
                        loaded = json.loads(path.read_text(encoding="utf-8"))
                        if not isinstance(loaded, dict):
                            raise ValueError("설정 파일은 JSON 객체여야 합니다.")
                        saved = loaded
                    saved.update({
                        "endpoint": endpoint,
                        "model": model,
                        "api_key": key,
                        "system": str(values["system"]),
                        "enter_sends": bool(values["enter_sends"]),
                        "tui_enabled": bool(values["tui_enabled"]),
                        "timeout": timeout,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "host": host,
                        "port": web_port,
                    })
                    save_settings(path, saved)
                    return saved
                except Exception as error:
                    message = f"오류: {error}"
                    return None

            try:
                curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
            except curses.error:
                pass
            while True:
                win.erase()
                win.box()
                try:
                    win.addstr(1, 2, "환경 설정", curses.A_BOLD | curses.color_pair(1))
                except curses.error:
                    pass
                visible_count = max(1, button_y - 3)
                max_first = max(0, field_count - visible_count)
                first_field = max(0, min(focus if focus < field_count else max_first, max_first))
                field_rows.clear()
                for index in range(first_field, min(field_count, first_field + visible_count)):
                    row = 2 + index - first_field
                    field_rows[index] = row
                    key, label, _ = fields[index]
                    value = values[key]
                    if key == "enter_sends":
                        shown = "Enter 전송 · Ctrl+J 줄바꿈" if value else "Enter 줄바꿈 · Ctrl+Enter 전송"
                    elif key in ("web_enabled", "tui_enabled"):
                        shown = "켜짐" if value else "꺼짐"
                    elif key == "api_key":
                        shown = "새 키 입력됨" if value else ("저장됨 (Ctrl+U 삭제)" if args.api_key and not clear_api_key else "미설정")
                        if clear_api_key:
                            shown = "삭제됨"
                    else:
                        shown = str(value).replace("\r", "").replace("\n", " ↵ ") if str(value) else "<API 기본값>"
                    prefix = "▶ " if focus == index else "  "
                    line = f"{prefix}{label:<18} : {shown}"
                    attr = curses.A_REVERSE if focus == index else 0
                    draw_modal_text(row, line, attr)
                draw_modal_text(button_y, " " * (width - 5))
                try:
                    win.addstr(button_y, save_x, save_text, curses.A_REVERSE if focus == save_focus else 0)
                    win.addstr(button_y, cancel_x, cancel_text, curses.A_REVERSE if focus == cancel_focus else 0)
                except curses.error:
                    pass
                draw_modal_text(status_y, message, curses.color_pair(3) if message.startswith("오류:") else 0)
                draw_modal_text(hint_y, f"설정 파일: {args.config_path} · UI 실행 설정은 다음 실행부터 적용")
                win.refresh()

                try:
                    key = win.get_wch()
                except curses.error:
                    continue
                if isinstance(key, int):
                    if key == curses.KEY_MOUSE:
                        try:
                            _, mx, my, _, bstate = curses.getmouse()
                            if not bstate & getattr(curses, "BUTTON1_CLICKED", 0):
                                continue
                            rel_y, rel_x = my - top, mx - left
                            clicked_field = next((i for i, row in field_rows.items() if row == rel_y), None)
                            if clicked_field is not None:
                                focus = clicked_field
                                continue
                            if rel_y == button_y and save_x <= rel_x < cancel_x:
                                focus = save_focus
                                key = "\n"
                            elif rel_y == button_y and rel_x >= cancel_x:
                                return None
                            else:
                                continue
                        except curses.error:
                            continue
                    elif key == curses.KEY_F2 or key == 27:
                        return None
                    elif key == curses.KEY_ENTER:
                        key = "\n"
                    elif key == curses.KEY_UP:
                        focus = max(0, focus - 1)
                        continue
                    elif key == curses.KEY_DOWN:
                        focus = min(cancel_focus, focus + 1)
                        continue
                    elif key == curses.KEY_BTAB:
                        focus = max(0, focus - 1)
                        continue
                    elif key == curses.KEY_BACKSPACE:
                        key = "\x7f"
                    elif key == curses.KEY_RESIZE:
                        return None
                    else:
                        continue
                if key == "\x1b":
                    return None
                if key == "\t":
                    focus = (focus + 1) % (cancel_focus + 1)
                    continue
                if key == "\x15":
                    if focus < field_count:
                        field_key = fields[focus][0]
                        if field_key == "api_key":
                            values["api_key"] = ""
                            clear_api_key = True
                        elif field_key not in ("web_enabled", "enter_sends", "tui_enabled"):
                            values[field_key] = ""
                    continue
                if key in ("\x7f", "\b"):
                    if focus < field_count:
                        field_key = fields[focus][0]
                        if field_key not in ("web_enabled", "enter_sends", "tui_enabled"):
                            values[field_key] = str(values[field_key])[:-1]
                            if field_key == "api_key":
                                clear_api_key = False
                    continue
                if key in ("\n", "\r"):
                    if focus == cancel_focus:
                        return None
                    if focus == save_focus:
                        saved = save_form()
                        if saved is not None:
                            return saved
                        continue
                    if focus < field_count and fields[focus][0] in ("web_enabled", "enter_sends", "tui_enabled"):
                        field_key = fields[focus][0]
                        values[field_key] = not bool(values[field_key])
                        continue
                    focus = min(cancel_focus, focus + 1)
                    continue
                if key == " " and focus < field_count and fields[focus][0] in ("web_enabled", "enter_sends", "tui_enabled"):
                    field_key = fields[focus][0]
                    values[field_key] = not bool(values[field_key])
                    continue
                if isinstance(key, str) and key.isprintable() and focus < field_count:
                    field_key = fields[focus][0]
                    if field_key == "api_key":
                        values[field_key] = str(values[field_key]) + key
                        clear_api_key = False
                    elif field_key not in ("web_enabled", "enter_sends", "tui_enabled"):
                        values[field_key] = str(values[field_key]) + key

        def activate_settings() -> None:
            nonlocal url, status, scroll_from_bottom, input_hint
            if busy:
                status = "응답이 끝난 뒤 설정을 변경하세요."
                return
            updated = edit_settings()
            if updated is None:
                return
            args.endpoint = updated["endpoint"]
            args.model = updated["model"]
            args.api_key = updated["api_key"]
            args.system = updated["system"]
            args.enter_sends = bool(updated["enter_sends"])
            args.tui_enabled = bool(updated["tui_enabled"])
            args.timeout = updated["timeout"]
            args.temperature = updated["temperature"]
            args.max_tokens = updated["max_tokens"]
            args.host = updated["host"]
            args.port = updated["port"]
            url = completion_url(args.endpoint)
            conversation.clear()
            scroll_from_bottom = 0
            input_hint = "Enter 전송 · Ctrl+J 줄바꿈" if args.enter_sends else "Enter 줄바꿈 · Ctrl+Enter 전송 (Ctrl+S 대체)"
            status = f"설정 저장 완료 · {input_hint} · TUI/웹 실행 설정은 다음 실행부터 적용"

        def transcript_lines(columns: int) -> list[tuple[str, int, int | None]]:
            rendered: list[tuple[str, int, int | None]] = []
            for message_index, message in enumerate(conversation):
                if message["role"] == "user":
                    rendered.extend((line, curses.color_pair(2), None) for line in user_bubble_lines(message["content"], columns))
                    rendered.append(("", 0, None))
                    continue
                thinking = str(message.get("thinking", ""))
                expanded = bool(message.get("thinking_expanded", False))
                if thinking:
                    label = "LLM · [생각 접기 · F3]" if expanded else "LLM · [생각 펼치기 · F3]"
                    rendered.append((label, curses.color_pair(1), message_index))
                    if expanded:
                        rendered.append(("  ┌─ 생각 ─", curses.color_pair(1), None))
                        rendered.extend(("  " + line, 0, None) for line in wrap_for_terminal(thinking, max(1, columns - 8)))
                        rendered.append(("  └────────", curses.color_pair(1), None))
                else:
                    rendered.append(("LLM", curses.color_pair(1), None))
                for line in wrap_for_terminal(message["content"], max(1, columns - 6)):
                    rendered.append(("  " + line, 0, None))
                rendered.append(("", 0, None))
            if busy:
                _, inline_thinking = split_thinking_text(streaming_raw)
                if streaming_thinking or inline_thinking:
                    stage = "추론 중"
                elif streaming_text:
                    stage = "텍스트 생성 중"
                elif server_connected:
                    stage = "서버 응답 연결 · 첫 텍스트 대기"
                else:
                    stage = "서버 연결 대기"
                dots = (".", "..", "...", "..")[int(time.monotonic() * 3) % 4]
                rendered.append((f"LLM · {stage}{dots}", curses.color_pair(1), None))
                for line in wrap_for_terminal(streaming_text, max(1, columns - 6)):
                    rendered.append(("  " + line, 0, None))
            return rendered

        def check_models(force_selection: bool = False) -> None:
            snapshot = {"endpoint": args.endpoint, "model": args.model, "api_key": args.api_key, "timeout": min(args.timeout, 8)}
            def worker() -> None:
                try:
                    from webchat import fetch_models_response, parse_available_models
                    available = parse_available_models(fetch_models_response(snapshot))
                    events.put(("models", (snapshot["endpoint"], snapshot["model"], available, force_selection)))
                except Exception as error:
                    events.put(("models_error", (snapshot["endpoint"], str(error))))
            threading.Thread(target=worker, daemon=True).start()

        check_models(force_selection=not args.model_explicit)
        while True:
            while True:
                try:
                    event, value = events.get_nowait()
                except queue.Empty:
                    break
                if event == "models":
                    endpoint, checked_model, available, force_selection = value
                    if endpoint != args.endpoint or checked_model != args.model or busy:
                        continue
                    if force_selection or checked_model not in available:
                        if not available:
                            status = "서버에 사용 가능한 모델이 없습니다. F2에서 설정을 확인하세요."
                        else:
                            selected = choose_model(available, checked_model)
                            screen.touchwin()
                            if selected is not None:
                                args.model = selected
                                status = f"모델 선택: {selected} · 현재 실행에 적용 (저장: F2 설정)"
                            else:
                                status = "모델 선택 취소 · F4로 목록 다시 확인"
                elif event == "recheck_models":
                    check_models()
                elif event == "models_error":
                    endpoint, detail = value
                    if endpoint == args.endpoint and not busy:
                        status = f"모델 목록 확인 실패: {detail} · F4 재시도"
                elif event == "token":
                    streaming_raw += value
                    streaming_text, _ = split_thinking_text(streaming_raw)
                elif event == "thinking":
                    streaming_thinking += value
                elif event == "connected":
                    server_connected = True
                elif event == "first_text":
                    first_text_seconds = value
                    status = f"첫 텍스트 도착 · {value:.2f}s · 응답 생성 중…"
                elif event == "done":
                    raw_answer = value["content"]
                    answer, tagged_thinking = split_thinking_text(raw_answer, final=True)
                    thinking = value.get("thinking", "") + tagged_thinking
                    metrics = value["metrics"]
                    conversation.append({"role": "assistant", "content": answer, "thinking": thinking, "thinking_expanded": False})
                    streaming_raw = ""
                    streaming_text = ""
                    streaming_thinking = ""
                    server_connected = False
                    busy = False
                    ttft = metrics["first_text_seconds"]
                    if ttft is None:
                        status = "응답 완료 · 출력 텍스트가 없습니다."
                    elif not metrics["streaming"]:
                        status = f"첫 텍스트 {ttft:.2f}s · 비스트리밍 tok/s 미측정 · 총 {metrics['total_seconds']:.2f}s · ↑/↓ 기록"
                    else:
                        rate = metrics["tokens_per_second"]
                        if rate is None:
                            rate_text = "속도 측정 불가"
                        else:
                            rate_text = f"{rate:.1f} tok/s" if metrics["exact_tokens"] else f"~{rate:.1f} tok/s (추정)"
                        thought_hint = " · 생각 F3/줄 클릭" if thinking else ""
                        status = f"첫 텍스트 {ttft:.2f}s · 생성 {metrics['generation_seconds']:.2f}s · {rate_text} · 총 {metrics['total_seconds']:.2f}s{thought_hint} · ↑/↓ 기록"
                elif event == "error":
                    streaming_raw = ""
                    streaming_text = ""
                    streaming_thinking = ""
                    server_connected = False
                    busy = False
                    status = f"API 오류: {value}"

            screen.erase()
            rows, columns = screen.getmaxyx()
            input_cells = max(1, columns - 7)
            input_rows = layout_input_text(input_text, input_cells)
            lines: list[tuple[str, int, int | None]] = []
            thinking_toggle_rows.clear()
            if rows < 12 or columns < 24:
                addstr(0, 0, "터미널을 조금 더 크게 늘려 주세요. (최소 24x12)", curses.A_BOLD)
                screen.refresh()
            else:
                title = f" lchat  │  {args.model}  │  {url} "
                settings_button = " [ 설정 ] "
                settings_x = max(0, columns - terminal_cell_width(settings_button) - 1)
                addstr(0, 0, title, curses.A_BOLD | curses.color_pair(1), settings_x - 1)
                addstr(0, settings_x, settings_button, curses.A_REVERSE | curses.color_pair(2))
                try:
                    screen.hline(1, 0, curses.ACS_HLINE, columns - 1)
                except curses.error:
                    pass
                composer_top = rows - 6
                body_start = 2
                body_height = max(1, composer_top - body_start)
                lines = transcript_lines(columns)
                max_scroll = max(0, len(lines) - body_height)
                scroll_from_bottom = min(max_scroll, max(0, scroll_from_bottom))
                end = len(lines) - scroll_from_bottom
                start = max(0, end - body_height)
                for row, (line, attr, toggle_index) in enumerate(lines[start:end], body_start):
                    addstr(row, 0, line, attr)
                    if toggle_index is not None:
                        thinking_toggle_rows[row] = toggle_index

                try:
                    screen.addstr(composer_top, 0, "┌" + "─" * (columns - 3) + "┐", curses.color_pair(1))
                    screen.addstr(composer_top + 4, 0, "└" + "─" * (columns - 3) + "┘", curses.color_pair(1))
                except curses.error:
                    pass
                visual_row, visual_column = input_cursor_position(input_rows, cursor)
                visible_start = max(0, len(input_rows) - 3)
                if visual_row < visible_start:
                    visible_start = visual_row
                elif visual_row >= visible_start + 3:
                    visible_start = visual_row - 2
                visible_start = max(0, min(visible_start, len(input_rows) - 1))
                for line_offset in range(3):
                    line_index = visible_start + line_offset
                    line_text = input_rows[line_index][0] if line_index < len(input_rows) else ""
                    input_y = composer_top + 1 + line_offset
                    prefix = " > " if line_index == 0 else "   "
                    padding = max(0, input_cells - terminal_cell_width(line_text)) + 1
                    addstr(input_y, 0, "│", curses.color_pair(1))
                    addstr(input_y, 1, prefix + line_text + " " * padding)
                    addstr(input_y, columns - 2, "│", curses.color_pair(1))
                footer = status
                if busy:
                    dots = (".", "..", "...", "..")[int(time.monotonic() * 3) % 4]
                    if first_text_seconds is not None:
                        _, inline_thinking = split_thinking_text(streaming_raw)
                        if streaming_thinking or inline_thinking:
                            stage = f"첫 텍스트 {first_text_seconds:.2f}s · 숨겨진 추론 중"
                        elif streaming_text:
                            stage = f"첫 텍스트 {first_text_seconds:.2f}s · 텍스트 출력 중"
                        else:
                            stage = f"첫 텍스트 {first_text_seconds:.2f}s · 응답 생성 중"
                    elif server_connected:
                        stage = "서버 응답 연결됨 · 추론/첫 텍스트 대기"
                    else:
                        stage = "서버 연결 대기"
                    footer = f"{stage}{dots} · Ctrl+C 종료"
                addstr(rows - 1, 0, footer, curses.color_pair(3) if status.startswith("API 오류:") else 0)
                cursor_y = composer_top + 1 + visual_row - visible_start
                cursor_x = min(columns - 3, 4 + visual_column)
                try:
                    screen.move(cursor_y, cursor_x)
                except curses.error:
                    pass
                screen.refresh()

            try:
                key = screen.get_wch()
            except curses.error:
                continue
            if key not in (curses.KEY_UP, curses.KEY_DOWN):
                vertical_goal_x = None
            if isinstance(key, int) and key == curses.KEY_ENTER:
                key = "\r"
            elif isinstance(key, int):
                if key == curses.KEY_MOUSE:
                    try:
                        _, mouse_x, mouse_y, _, mouse_state = curses.getmouse()
                        click_mask = getattr(curses, "BUTTON1_CLICKED", 0) | getattr(curses, "BUTTON1_PRESSED", 0)
                        if mouse_y == 0 and mouse_x >= settings_x and mouse_state & click_mask:
                            activate_settings()
                        elif mouse_y in thinking_toggle_rows and mouse_state & click_mask:
                            message = conversation[thinking_toggle_rows[mouse_y]]
                            delta_rows = len(wrap_for_terminal(str(message.get("thinking", "")), max(1, columns - 8))) + 2
                            was_expanded = bool(message.get("thinking_expanded", False))
                            message["thinking_expanded"] = not was_expanded
                            scroll_from_bottom = max(0, scroll_from_bottom - delta_rows) if was_expanded else scroll_from_bottom + delta_rows
                            status = "생각을 펼쳤습니다." if message["thinking_expanded"] else "생각을 접었습니다."
                        elif 2 <= mouse_y < rows - 6:
                            wheel_up = getattr(curses, "BUTTON4_PRESSED", 0) | getattr(curses, "BUTTON4_CLICKED", 0)
                            wheel_down = getattr(curses, "BUTTON5_PRESSED", 0) | getattr(curses, "BUTTON5_CLICKED", 0)
                            amount = max(1, body_height // 3)
                            if wheel_up and mouse_state & wheel_up:
                                scroll_from_bottom = min(max(0, len(lines) - body_height), scroll_from_bottom + amount)
                            elif wheel_down and mouse_state & wheel_down:
                                scroll_from_bottom = max(0, scroll_from_bottom - amount)
                    except curses.error:
                        pass
                    continue
                if key == curses.KEY_F2:
                    activate_settings()
                    continue
                if key == curses.KEY_F3:
                    toggle_latest_thinking()
                    continue
                if key == curses.KEY_F4:
                    status = "서버 모델 목록 확인 중…"
                    check_models(force_selection=True)
                    continue
                if key in (3, 4):
                    return 0
                if key == 21:
                    input_text = ""
                    cursor = 0
                    continue
                if key == curses.KEY_RESIZE:
                    continue
                if key in (curses.KEY_LEFT,):
                    cursor = max(0, cursor - 1)
                elif key in (curses.KEY_RIGHT,):
                    cursor = min(len(input_text), cursor + 1)
                elif key == curses.KEY_HOME:
                    cursor = 0
                elif key == curses.KEY_END:
                    cursor = len(input_text)
                elif key in (curses.KEY_BACKSPACE,):
                    if cursor:
                        input_text = input_text[:cursor - 1] + input_text[cursor:]
                        cursor -= 1
                elif key == curses.KEY_DC:
                    input_text = input_text[:cursor] + input_text[cursor + 1:]
                elif key == curses.KEY_PPAGE:
                    scroll_from_bottom = min(max(0, len(lines) - body_height), scroll_from_bottom + max(1, body_height - 2))
                elif key == curses.KEY_NPAGE:
                    scroll_from_bottom = max(0, scroll_from_bottom - max(1, body_height - 2))
                elif key == curses.KEY_UP:
                    move_vertical(-1, input_rows)
                elif key == curses.KEY_DOWN:
                    move_vertical(1, input_rows)
                continue

            if key in ("\x03", "\x04"):
                return 0
            if key in ("\x7f", "\b"):
                if cursor:
                    input_text = input_text[:cursor - 1] + input_text[cursor:]
                    cursor -= 1
                continue
            if key == "\x15":
                input_text = ""
                cursor = 0
                continue
            should_send = key == "\x13" or (key == "\r" if args.enter_sends else key == "\n")
            should_insert_newline = (key == "\n" if args.enter_sends else key == "\r")
            if should_insert_newline:
                input_text = input_text[:cursor] + "\n" + input_text[cursor:]
                cursor += 1
                scroll_from_bottom = 0
                continue
            if should_send:
                request_started_at = time.monotonic()
                text = input_text.strip()
                if not text or busy:
                    continue
                input_text = ""
                cursor = 0
                if not prompt_history or prompt_history[-1] != text:
                    prompt_history.append(text)
                history_index = len(prompt_history)
                history_draft = ""
                if text.lower() in ("/exit", "/quit"):
                    return 0
                if text.lower() == "/help":
                    status = f"{input_hint} · ↑/↓ 입력 기록 · PgUp/PgDn 스크롤 · /reset · /exit · F2 설정 · Ctrl+U 지우기"
                    continue
                if text.lower() == "/reset":
                    conversation.clear()
                    scroll_from_bottom = 0
                    status = "대화 기록을 지웠습니다."
                    continue
                if not args.model:
                    input_text = text
                    cursor = len(text)
                    status = "모델이 선택되지 않았습니다. F4로 목록을 확인하세요."
                    continue
                conversation.append({"role": "user", "content": text})
                payload: dict[str, Any] = {
                    "model": args.model,
                    "messages": [{"role": "system", "content": args.system}, *({"role": item["role"], "content": item["content"]} for item in conversation)],
                    "stream": not args.no_stream,
                }
                if args.temperature is not None:
                    payload["temperature"] = args.temperature
                if args.max_tokens is not None:
                    payload["max_tokens"] = args.max_tokens
                streaming_raw = ""
                streaming_text = ""
                streaming_thinking = ""
                server_connected = False
                busy = True
                scroll_from_bottom = 0
                status = "서버 연결 대기…"
                first_text_seconds = None
                threading.Thread(target=generate, args=(payload, request_started_at), daemon=True).start()
                continue
            if key.isprintable():
                input_text = input_text[:cursor] + key + input_text[cursor:]
                cursor += len(key)
                scroll_from_bottom = 0

    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    try:
        return curses.wrapper(tui)
    except KeyboardInterrupt:
        return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.model_explicit = bool(args.model and args.model.strip())
    config_path = (Path(args.config).expanduser() if args.config else DEFAULT_CONFIG_PATH).resolve()
    if args.cfg:
        print(config_path)
        return 0
    if args.reset:
        try:
            save_settings(config_path, DEFAULT_SETTINGS)
        except OSError as error:
            parser.error(f"Could not reset config file {config_path}: {error}")
        print(f"설정을 기본값으로 초기화했습니다: {config_path}", flush=True)

    config: dict[str, Any] = DEFAULT_SETTINGS.copy()
    if config_path.exists():
        try:
            saved = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            parser.error(f"Could not read config file {config_path}: {error}")
        if not isinstance(saved, dict):
            parser.error(f"Config file must contain a JSON object: {config_path}")
        config.update({key: value for key, value in saved.items() if key in config})
    env_values = {
        "OPENAI_BASE_URL": ("endpoint", str),
        "OPENAI_MODEL": ("model", str),
        "OPENAI_API_KEY": ("api_key", str),
        "LCHAT_SYSTEM_PROMPT": ("system", str),
        "LCHAT_HOST": ("host", str),
        "LCHAT_PORT": ("port", int),
        "LCHAT_TIMEOUT": ("timeout", float),
        "LCHAT_TEMPERATURE": ("temperature", float),
        "LCHAT_MAX_TOKENS": ("max_tokens", int),
    }
    try:
        for env_name, (key, convert) in env_values.items():
            if env_name in os.environ:
                config[key] = convert(os.environ[env_name])
    except ValueError as error:
        parser.error(f"Invalid environment setting: {error}")
    for key in ("endpoint", "model", "api_key", "system", "host", "port", "timeout", "temperature", "max_tokens"):
        value = getattr(args, key)
        if value is not None:
            config[key] = value
    if config.get("port") is not None and not args.terminal_only and not args.plain:
        command = [sys.executable, str(Path(__file__).resolve().parent / "webchat.py"), "--config", str(config_path)]
        for key in ("endpoint", "model", "system", "host", "port", "timeout", "temperature", "max_tokens"):
            value = getattr(args, key)
            if value is not None:
                command.extend(("--" + key.replace("_", "-"), str(value)))
        if args.tui:
            command.append("--tui")
        if args.no_tui:
            command.append("--no-tui")
        if args.no_stream:
            command.append("--no-stream")
        child_env = os.environ.copy()
        if args.api_key is not None:
            child_env["OPENAI_API_KEY"] = args.api_key
        return subprocess.call(command, env=child_env)
    if args.no_tui:
        args.plain = True
    try:
        config["timeout"] = float(config["timeout"])
        config["temperature"] = None if config["temperature"] in (None, "") else float(config["temperature"])
        config["max_tokens"] = None if config["max_tokens"] in (None, "") else int(config["max_tokens"])
    except (TypeError, ValueError):
        parser.error("Invalid timeout/temperature/max_tokens value in config or environment.")
    args.endpoint = config["endpoint"]
    args.model = config["model"]
    args.api_key = config["api_key"]
    args.system = config["system"]
    if not isinstance(config["enter_sends"], bool):
        parser.error("enter_sends in config must be true or false.")
    if not isinstance(config["tui_enabled"], bool):
        parser.error("tui_enabled in config must be true or false.")
    args.enter_sends = config["enter_sends"]
    args.tui_enabled = config["tui_enabled"]
    args.timeout = config["timeout"]
    args.temperature = config["temperature"]
    args.max_tokens = config["max_tokens"]
    args.host = config["host"]
    args.port = config["port"]
    args.config_path = config_path

    if not args.endpoint:
        parser.error("--endpoint is required (or set OPENAI_BASE_URL/config.json).")
    interactive_tty = args.tui or (not args.plain and args.tui_enabled and sys.stdin.isatty() and sys.stdout.isatty())
    if not args.model and not interactive_tty:
        parser.error("--model is required in plain/non-interactive mode; use the TUI settings to select a model.")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero.")
    if args.max_tokens is not None and args.max_tokens <= 0:
        parser.error("--max-tokens must be greater than zero.")

    try:
        url = completion_url(args.endpoint)
    except ValueError as error:
        parser.error(str(error))

    use_tui = interactive_tty
    if use_tui:
        try:
            return run_tui(args, url)
        except ImportError as error:
            if args.tui:
                parser.error(f"ncurses TUI is unavailable: {error}")
            print(f"[info] ncurses unavailable ({error}); falling back to plain mode.", file=sys.stderr)
        except Exception as error:
            import curses
            if isinstance(error, curses.error):
                if args.tui:
                    parser.error(f"Could not start ncurses TUI: {error}")
                print(f"[info] ncurses could not initialize ({error}); falling back to plain mode.", file=sys.stderr)
            else:
                raise

    if not args.model:
        parser.error("ncurses를 사용할 수 없어 모델 설정 창을 열지 못했습니다. --model 또는 config.json을 설정하세요.")
    print(f"lchat · model: {args.model}")
    print(f"Endpoint: {url}")
    print("Type /help for commands, /exit to quit.\n")
    messages: list[dict[str, str]] = [{"role": "system", "content": args.system}]

    while True:
        try:
            user_text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            return 0
        if not user_text:
            continue
        if readline is not None and user_text:
            history_length = readline.get_current_history_length()
            if history_length == 0 or readline.get_history_item(history_length) != user_text:
                readline.add_history(user_text)
        if user_text.lower() in ("/exit", "/quit"):
            print("Bye!")
            return 0
        if user_text.lower() == "/help":
            print("Commands: /help  /reset  /exit (or /quit)")
            continue
        if user_text.lower() == "/reset":
            messages = [{"role": "system", "content": args.system}]
            print("Conversation cleared.\n")
            continue

        print_user_bubble(user_text)
        messages.append({"role": "user", "content": user_text})
        payload: dict[str, Any] = {
            "model": args.model,
            "messages": messages,
            "stream": not args.no_stream,
        }
        if args.temperature is not None:
            payload["temperature"] = args.temperature
        if args.max_tokens is not None:
            payload["max_tokens"] = args.max_tokens

        print("assistant> ", end="", flush=True)
        try:
            answer = complete(url, payload, args.api_key, args.timeout)
        except (RuntimeError, OSError) as error:
            # Remove the unsent/failed user turn so a retry doesn't duplicate it.
            messages.pop()
            print(f"\n[error] {error}\n")
            continue
        messages.append({"role": "assistant", "content": answer})
        print()


if __name__ == "__main__":
    raise SystemExit(main())
