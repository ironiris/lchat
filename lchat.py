#!/usr/bin/env python3
"""A small terminal chatbot for OpenAI-compatible chat completion APIs."""

from __future__ import annotations

import argparse
import json
import locale
import math
import os
import queue
import re
import shutil
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


__version__ = "0.3.0"
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
    "disable_qwen_thinking": False,
}


def is_qwen_model(model: str) -> bool:
    return "qwen" in model.casefold()


def apply_qwen_thinking_setting(payload: dict[str, Any], model: str, disable_qwen_thinking: bool) -> None:
    if is_qwen_model(model):
        payload["enable_thinking"] = not disable_qwen_thinking


def parse_bool(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"expected true/false, got {value!r}")


def save_settings(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, temp_name = tempfile.mkstemp(prefix=".config-", dir=path.parent)
    try:
        if hasattr(os, "fchmod"):
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
    parser.add_argument("--version", action="version", version=f"lchat {__version__}")
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
    parser.add_argument("--config", nargs="?", const="", metavar="FILE",
                        help="Use FILE; without FILE, print the default settings-file path and exit")
    parser.add_argument("--reset", action="store_true", help="Reset settings to defaults before running")
    parser.add_argument("--no-stream", action="store_true", help="Wait for the complete answer before displaying it")
    parser.add_argument("--log", action="store_true", help="Save raw API responses and decoded text under ./log (may contain sensitive data)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--tui", action="store_true", help="Force the full-screen Textual terminal UI")
    mode.add_argument("--plain", action="store_true", help="Use the simple line-oriented terminal UI (without web hosting)")
    mode.add_argument("--no-tui", action="store_true", help="Disable TUI (web-only when web hosting is enabled)")
    parser.add_argument("--terminal-only", action="store_true",
                        help="Run terminal only; skip configured web hosting")
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


def wrap_markdown_bold(text: str, max_width: int) -> list[list[tuple[str, bool]]]:
    """Wrap text while removing paired ** markers and preserving bold spans."""
    max_width = max(1, max_width)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    styled: list[tuple[str, bool]] = []
    position = 0
    while position < len(text):
        opening = text.find("**", position)
        if opening < 0:
            styled.append((text[position:], False))
            break
        closing = text.find("**", opening + 2)
        if closing < 0:
            styled.append((text[position:], False))
            break
        if opening > position:
            styled.append((text[position:opening], False))
        if closing > opening + 2:
            styled.append((text[opening + 2:closing], True))
        position = closing + 2

    paragraphs: list[list[tuple[str, bool]]] = [[]]
    for segment, bold in styled:
        for char in segment:
            if char == "\n":
                paragraphs.append([])
            elif char == "\t":
                paragraphs[-1].extend((" ", bold) for _ in range(4))
            else:
                paragraphs[-1].append((char, bold))

    def compact(chars: list[tuple[str, bool]]) -> list[tuple[str, bool]]:
        result: list[tuple[str, bool]] = []
        for char, bold in chars:
            if result and result[-1][1] == bold:
                result[-1] = (result[-1][0] + char, bold)
            else:
                result.append((char, bold))
        return result

    wrapped: list[list[tuple[str, bool]]] = []
    for paragraph in paragraphs:
        words: list[list[tuple[str, bool]]] = []
        word: list[tuple[str, bool]] = []
        for char, bold in paragraph:
            if char.isspace():
                if word:
                    words.append(word)
                    word = []
            else:
                word.append((char, bold))
        if word:
            words.append(word)

        line: list[tuple[str, bool]] = []
        line_width = 0
        for word in words:
            chunks: list[list[tuple[str, bool]]] = []
            chunk: list[tuple[str, bool]] = []
            chunk_width = 0
            for char, bold in word:
                char_width = terminal_cell_width(char)
                if chunk and chunk_width + char_width > max_width:
                    chunks.append(chunk)
                    chunk = []
                    chunk_width = 0
                chunk.append((char, bold))
                chunk_width += char_width
            if chunk or not chunks:
                chunks.append(chunk)

            for index, part in enumerate(chunks):
                separator = [(" ", False)] if line and index == 0 else []
                part_width = sum(terminal_cell_width(char) for char, _ in part)
                if line and line_width + len(separator) + part_width > max_width:
                    wrapped.append(compact(line))
                    line = part.copy()
                    line_width = part_width
                else:
                    line.extend(separator)
                    line.extend(part)
                    line_width += len(separator) + part_width
                if index < len(chunks) - 1:
                    wrapped.append(compact(line))
                    line = []
                    line_width = 0
        wrapped.append(compact(line))
    return wrapped or [[]]


def markdown_heading_text(line: str) -> str | None:
    indent = len(line) - len(line.lstrip(" "))
    if indent > 3:
        return None
    content = line[indent:]
    marker_count = len(content) - len(content.lstrip("#"))
    if not 1 <= marker_count <= 6 or marker_count == len(content):
        return None
    if content[marker_count] not in (" ", "\t"):
        return None
    title = content[marker_count:].strip()
    if title.endswith("#"):
        without_closing = title.rstrip("#")
        if without_closing.endswith((" ", "\t")):
            title = without_closing.rstrip()
    return title or None


def is_markdown_horizontal_rule(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) < 3:
        return False
    marker = stripped[0]
    return marker in "-*_" and sum(char == marker for char in stripped) >= 3 and all(
        char == marker or char.isspace() for char in stripped
    )


def render_latex_math(source: str, max_width: int) -> list[str]:
    """Render display math with SymPy, falling back for a simple fraction."""
    max_width = max(1, max_width)
    try:
        from sympy import pretty
        from sympy.parsing.latex import parse_latex

        if re.search(r"\\(?:propto|approx|simeq|sim|neq|leq|geq|equiv)\b", source):
            raise ValueError("Use the plain-text fallback for relation symbols.")
        expression = parse_latex(source.strip())
        lines = pretty(expression, use_unicode=True, wrap_line=False).splitlines()
    except Exception:
        replacements = {
            r"\lambda": "λ", r"\alpha": "α", r"\beta": "β",
            r"\gamma": "γ", r"\pi": "π", r"\theta": "θ",
            r"\mu": "μ", r"\sigma": "σ", r"\omega": "ω",
            r"\propto": "∝", r"\approx": "≈", r"\simeq": "≃", r"\sim": "∼",
            r"\neq": "≠", r"\leq": "≤", r"\geq": "≥", r"\equiv": "≡",
            r"\times": "×", r"\cdot": "·",
            r"\,": " ", r"\left": "", r"\right": "",
        }

        def convert_symbols(text: str) -> str:
            for command, symbol in replacements.items():
                text = text.replace(command, symbol)
            superscripts = str.maketrans("0123456789+-=()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾")
            subscripts = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
            text = re.sub(
                r"\^\{([0-9+\-=()]*)\}|\^([0-9+\-=()]+)",
                lambda match: (match.group(1) or match.group(2)).translate(superscripts),
                text,
            )
            return re.sub(
                r"_\{([0-9+\-=()]*)\}|_([0-9+\-=()]+)",
                lambda match: (match.group(1) or match.group(2)).translate(subscripts),
                text,
            )

        fraction = re.search(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", source)
        if fraction:
            numerator = convert_symbols(fraction.group(1))
            denominator = convert_symbols(fraction.group(2))
            prefix = convert_symbols(source[:fraction.start()])
            suffix = convert_symbols(source[fraction.end():])
            fraction_width = max(3, terminal_cell_width(numerator), terminal_cell_width(denominator))
            prefix_width = terminal_cell_width(prefix)

            def pad_fraction(text: str) -> str:
                spare = fraction_width - terminal_cell_width(text)
                return " " * (spare // 2) + text + " " * (spare - spare // 2)

            lines = [
                prefix + pad_fraction(numerator),
                " " * prefix_width + "─" * fraction_width,
                " " * prefix_width + pad_fraction(denominator) + suffix,
            ]
            block_width = max(terminal_cell_width(line) for line in lines)
            if block_width <= max_width:
                padding = " " * ((max_width - block_width) // 2)
                return [padding + line for line in lines]

        plain = convert_symbols(source)
        return wrap_for_terminal(plain, max_width)

    lines = [line.rstrip() for line in lines]
    nonempty = [line for line in lines if line]
    if not nonempty:
        return [""]
    common_indent = min(len(line) - len(line.lstrip(" ")) for line in nonempty)
    lines = [line[common_indent:] if line else "" for line in lines]
    block_width = max(terminal_cell_width(line) for line in lines)
    if block_width > max_width:
        return lines
    return [" " * ((max_width - terminal_cell_width(line)) // 2) + line for line in lines]


def split_markdown_table_row(line: str) -> list[str] | None:
    if "|" not in line:
        return None
    content = line.strip()
    if content.startswith("|"):
        content = content[1:]
    if content.endswith("|"):
        content = content[:-1]
    return [cell.strip() for cell in content.split("|")]


def render_markdown_table(paragraphs: list[str], start: int, max_width: int) -> tuple[list[list[tuple[str, bool]]], int] | None:
    header = split_markdown_table_row(paragraphs[start])
    if not header or start + 1 >= len(paragraphs):
        return None
    separators = split_markdown_table_row(paragraphs[start + 1])
    if not separators or len(separators) != len(header):
        return None
    if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separators):
        return None

    alignments = [
        "center" if cell.startswith(":") and cell.endswith(":")
        else "right" if cell.endswith(":") else "left"
        for cell in separators
    ]
    rows = [header]
    next_index = start + 2
    while next_index < len(paragraphs):
        row = split_markdown_table_row(paragraphs[next_index])
        if row is None or len(row) != len(header):
            break
        rows.append(row)
        next_index += 1

    widths = []
    for column in range(len(header)):
        widest = 1
        for row in rows:
            runs = wrap_markdown_bold(row[column], 1_000_000)[0]
            widest = max(widest, sum(terminal_cell_width(text) for text, _ in runs))
        widths.append(widest)

    def table_width() -> int:
        return sum(widths) + 3 * len(widths) + 1

    while table_width() > max_width and max(widths) > 1:
        widest_column = max(range(len(widths)), key=widths.__getitem__)
        widths[widest_column] -= 1
    if table_width() > max_width:
        return None

    def border(left: str, joint: str, right: str) -> list[tuple[str, bool]]:
        line = "  " + left
        for index, width in enumerate(widths):
            line += "─" * (width + 2)
            line += right if index == len(widths) - 1 else joint
        return [(line, False)]

    output = [border("┌", "┬", "┐")]
    for row_index, row in enumerate(rows):
        wrapped_cells = [wrap_markdown_bold(cell, widths[column]) for column, cell in enumerate(row)]
        height = max(len(cell_lines) for cell_lines in wrapped_cells)
        for line_index in range(height):
            segments: list[tuple[str, bool]] = [("  │", False)]
            for column, cell_lines in enumerate(wrapped_cells):
                runs = cell_lines[line_index] if line_index < len(cell_lines) else []
                text_width = sum(terminal_cell_width(text) for text, _ in runs)
                spare = widths[column] - text_width
                left_padding = spare if alignments[column] == "right" else spare // 2 if alignments[column] == "center" else 0
                right_padding = spare - left_padding
                segments.append((" " * (left_padding + 1), False))
                segments.extend(runs)
                segments.append((" " * (right_padding + 1) + "│", False))
            output.append(segments)
        if row_index == 0:
            output.append(border("├", "┼", "┤"))
    output.append(border("└", "┴", "┘"))
    return output, next_index


def wrap_assistant_markdown(text: str, max_width: int) -> list[tuple[list[tuple[str, bool]], str]]:
    """Wrap assistant text and render common Markdown and display math."""
    max_width = max(1, max_width)
    paragraphs = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    rendered: list[tuple[list[tuple[str, bool]], str]] = []
    in_code_block = False
    in_math_block = False
    math_lines: list[str] = []
    skip_until = 0
    for paragraph_index, paragraph in enumerate(paragraphs):
        if paragraph_index < skip_until:
            continue
        if in_math_block:
            if paragraph.strip() == r"\]":
                rendered.extend(([("  " + line, False)], "math") for line in render_latex_math(" ".join(math_lines), max_width))
                in_math_block = False
                math_lines.clear()
            else:
                math_lines.append(paragraph)
            continue

        is_fence = paragraph.lstrip(" \t").startswith("```")
        if in_code_block or is_fence:
            rendered.extend(([(line, False)], "body") for line in wrap_for_terminal(paragraph, max_width))
            if is_fence:
                in_code_block = not in_code_block
            continue

        stripped = paragraph.strip()
        if stripped == r"\[":
            in_math_block = True
            math_lines.clear()
            continue
        if stripped.startswith(r"\[") and stripped.endswith(r"\]") and len(stripped) >= 4:
            source = stripped[2:-2]
            rendered.extend(([("  " + line, False)], "math") for line in render_latex_math(source, max_width))
            continue

        table = render_markdown_table(paragraphs, paragraph_index, max_width)
        if table is not None:
            table_lines, skip_until = table
            rendered.extend((line, "table") for line in table_lines)
            continue

        if is_markdown_horizontal_rule(paragraph):
            rendered.append(([ ("  " + "─" * max_width, False) ], "rule"))
            continue

        title = markdown_heading_text(paragraph)
        if title is None:
            if "**" not in paragraph:
                rendered.extend(([(line, False)], "body") for line in wrap_for_terminal(paragraph, max_width))
            else:
                rendered.extend((line, "body") for line in wrap_markdown_bold(paragraph, max_width))
            continue

        title_lines = wrap_markdown_bold(title, max(1, max_width - 4))
        inner_width = max(
            1,
            *(sum(terminal_cell_width(segment) for segment, _ in line) for line in title_lines),
        )
        top = "  ┌" + "─" * (inner_width + 2) + "┐"
        bottom = "  └" + "─" * (inner_width + 2) + "┘"
        rendered.append(([(top, False)], "box"))
        for line in title_lines:
            line_width = sum(terminal_cell_width(segment) for segment, _ in line)
            content = [("  │ ", False), *((segment, True) for segment, _ in line)]
            content.append((" " * (inner_width - line_width) + " │", False))
            rendered.append((content, "box"))
        rendered.append(([(bottom, False)], "box"))

    if in_math_block:
        raw_math = "\\[\n" + "\n".join(math_lines)
        rendered.extend(([(line, False)], "body") for line in wrap_for_terminal(raw_math, max_width))
    return rendered


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


class ResponseLog:
    """Opt-in response-only debug files; never records requests or headers."""

    def __init__(self, directory: Path | None, streaming: bool) -> None:
        self.directory = directory
        self.streaming = streaming
        self.raw: Any = None
        self.answer: Any = None
        self.thinking: Any = None
        self.base: Path | None = None

    def __enter__(self) -> ResponseLog:
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            prefix = time.strftime("response-%Y%m%d-%H%M%S-")
            fd, filename = tempfile.mkstemp(prefix=prefix, suffix=".sse" if self.streaming else ".json", dir=self.directory)
            self.base = Path(filename).with_suffix("")
            self.raw = os.fdopen(fd, "wb")
        return self

    def __exit__(self, *args: Any) -> None:
        for file in (self.raw, self.answer, self.thinking):
            if file is not None:
                file.close()

    def _append(self, kind: str, value: Any) -> None:
        if not self.base:
            return
        if isinstance(value, list):
            for item in value:
                self._append(kind, item)
            return
        if isinstance(value, dict):
            self._append(kind, value.get("text", value.get("content", "")))
            return
        if not isinstance(value, str) or not value:
            return
        file = getattr(self, kind)
        if file is None:
            fd = os.open(str(self.base) + f".{kind}.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            file = os.fdopen(fd, "w", encoding="utf-8")
            setattr(self, kind, file)
        file.write(value)
        file.flush()

    def write(self, data: bytes) -> None:
        if self.raw is None:
            return
        self.raw.write(data)
        self.raw.flush()
        try:
            if self.streaming:
                line = data.decode("utf-8").strip()
                if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                    return
                event = json.loads(line[5:].strip())
            else:
                event = json.loads(data.decode("utf-8"))
            choices = event.get("choices") or []
            if not choices:
                return
            message = choices[0].get("delta" if self.streaming else "message") or {}
            self._append("answer", message.get("content"))
            for key in ("reasoning_content", "reasoning", "thinking", "analysis"):
                self._append("thinking", message.get(key))
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError, IndexError):
            pass  # Preserve malformed/unrecognized data in the raw log.


def stream_answer(response: Any, log: ResponseLog | None = None) -> str:
    pieces: list[str] = []
    for raw_line in response:
        if log:
            log.write(raw_line)
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


def complete(url: str, payload: dict[str, Any], api_key: str, timeout: float, log_dir: Path | None = None) -> str:
    request = make_request(url, payload, api_key)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, ResponseLog(log_dir, bool(payload.get("stream"))) as log:
            if payload.get("stream"):
                return stream_answer(response, log)
            body = response.read()
            log.write(body)
            result = json.loads(body.decode("utf-8"))
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


def generate(args: argparse.Namespace, url: str, events: queue.Queue, payload: dict[str, Any], request_started_at: float) -> None:
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
        with response_context as response, ResponseLog(getattr(args, "log_dir", None), bool(payload["stream"])) as log:
            events.put(("connected", None))
            if payload["stream"]:
                for raw_line in response:
                    log.write(raw_line)
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
                body = response.read()
                log.write(body)
                result = json.loads(body.decode("utf-8"))
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
    except Exception as error:
        # Malformed or unexpected server responses must not leave the UI busy forever.
        events.put(("error", str(error)))



def run_tui(args: argparse.Namespace, url: str) -> int:
    import textual
    version = tuple(int(part) for part in textual.__version__.split(".")[:3])
    if not (2, 1, 2) <= version < (3, 0, 0):
        raise ImportError(f"Textual 2.1.2 이상 3.0 미만이 필요합니다 (설치된 버전: {textual.__version__}).")
    from lchat_tui import ChatApp
    ChatApp(args, url).run()
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.model_explicit = bool(args.model and args.model.strip())
    args.log_dir = Path.cwd() / "log" if args.log else None
    if args.log_dir is not None:
        try:
            args.log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as error:
            parser.error(f"로그 디렉터리를 만들 수 없습니다: {error}")
    config_path = (Path(args.config).expanduser() if args.config else DEFAULT_CONFIG_PATH).resolve()
    if args.config == "":
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
        "LCHAT_DISABLE_QWEN_THINKING": ("disable_qwen_thinking", parse_bool),
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
    if os.name == "nt" and config.get("port") is not None and not args.terminal_only and not args.plain:
        print("[info] Windows에서는 웹 호스팅을 건너뛰고 터미널 모드로 실행합니다.", file=sys.stderr)
    if config.get("port") is not None and os.name != "nt" and not args.terminal_only and not args.plain:
        command = [sys.executable, str(Path(__file__).resolve().parent / "lchat_web.py"), "--config", str(config_path)]
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
        if args.log:
            command.append("--log")
        child_env = os.environ.copy()
        if args.api_key is not None:
            child_env["OPENAI_API_KEY"] = args.api_key
        os.execvpe(sys.executable, command, child_env)
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
    if not isinstance(config["disable_qwen_thinking"], bool):
        parser.error("disable_qwen_thinking in config must be true or false.")
    args.enter_sends = config["enter_sends"]
    args.tui_enabled = config["tui_enabled"]
    args.disable_qwen_thinking = config["disable_qwen_thinking"]
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
            parser.error(f"Textual TUI를 사용할 수 없습니다. pip install -r requirements.txt: {error}")

    if not args.model:
        parser.error("Textual을 사용할 수 없어 모델 설정 창을 열지 못했습니다. --model 또는 config.json을 설정하세요.")
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
        apply_qwen_thinking_setting(payload, args.model, args.disable_qwen_thinking)

        print("assistant> ", end="", flush=True)
        try:
            answer = complete(url, payload, args.api_key, args.timeout, args.log_dir)
        except (RuntimeError, OSError) as error:
            # Remove the unsent/failed user turn so a retry doesn't duplicate it.
            messages.pop()
            print(f"\n[error] {error}\n")
            continue
        messages.append({"role": "assistant", "content": answer})
        print()


if __name__ == "__main__":
    raise SystemExit(main())
