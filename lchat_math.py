"""Small, dependency-free LaTeX-to-Unicode renderer for terminal Markdown.

This deliberately renders a useful subset rather than pretending to be a full
TeX engine. Unknown commands are retained so information is never silently lost.
"""
from __future__ import annotations

import re
import unicodedata

SYMBOLS = {
    "pm": "±", "mp": "∓", "times": "×", "cdot": "·", "div": "÷",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "neq": "≠", "ll": "≪", "gg": "≫",
    "approx": "≈", "equiv": "≡", "propto": "∝", "sim": "∼",
    "infty": "∞", "pi": "π", "alpha": "α", "beta": "β", "gamma": "γ",
    "delta": "δ", "Delta": "Δ", "theta": "θ", "lambda": "λ", "mu": "μ", "Omega": "Ω",
    "sigma": "σ", "omega": "ω", "phi": "φ", "epsilon": "ε", "sum": "∑",
    "int": "∫", "in": "∈", "to": "→", "rightarrow": "→", "ldots": "…",
    "sin": "sin", "cos": "cos", "tan": "tan", "log": "log", "ln": "ln",
}
SUPERSCRIPT = str.maketrans("0123456789+-=()in", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁱⁿ")
SUBSCRIPT = str.maketrans("0123456789+-=()aehijklmnoprstuvx", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ")


def group(source: str, pos: int) -> tuple[str, int] | None:
    """Read a balanced brace group; return None for incomplete streaming input."""
    while pos < len(source) and source[pos].isspace():
        pos += 1
    if pos >= len(source) or source[pos] != "{":
        return None
    start = pos + 1
    depth = 1
    pos += 1
    while pos < len(source):
        if source[pos] == "\\" and pos + 1 < len(source) and source[pos + 1] in "{}":
            pos += 2
            continue
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                return source[start:pos], pos + 1
        pos += 1
    return None


def unicode_math(source: str) -> str:
    """Convert common TeX commands, nested groups, roots and fractions."""
    out: list[str] = []
    i = 0
    while i < len(source):
        char = source[i]
        if char == "\\":
            match = re.match(r"\\([a-zA-Z]+|.)", source[i:])
            if not match:
                out.append(char)
                i += 1
                continue
            command = match.group(1)
            end = i + len(match.group(0))
            if command in ("frac", "dfrac", "tfrac"):
                numerator = group(source, end)
                denominator = group(source, numerator[1]) if numerator else None
                if numerator and denominator:
                    out.append(f"({unicode_math(numerator[0])})/({unicode_math(denominator[0])})")
                    i = denominator[1]
                    continue
            elif command == "sqrt":
                index = ""
                if end < len(source) and source[end] == "[":
                    close = source.find("]", end + 1)
                    if close >= 0:
                        index = unicode_math(source[end + 1:close])
                        end = close + 1
                radicand = group(source, end)
                if radicand:
                    value = unicode_math(radicand[0])
                    out.append(("√" if not index else index + "√") + (value if len(value) == 1 else f"({value})"))
                    i = radicand[1]
                    continue
            elif command in ("text", "mathrm", "mathbf", "mathit", "operatorname", "boxed"):
                argument = group(source, end)
                if argument:
                    out.append(unicode_math(argument[0]))
                    i = argument[1]
                    continue
            if command in SYMBOLS:
                out.append(SYMBOLS[command])
            elif command in ("left", "right", "displaystyle"):
                pass
            elif command in (",", ";", " ", "quad", "qquad"):
                out.append(" ")
            elif command in ("{", "}", "_", "%", "$", "#"):
                out.append(command)
            else:
                out.append("\\" + command)
                argument = group(source, end)
                if argument:
                    out.append(source[end:argument[1]])
                    end = argument[1]
            i = end
        elif char in "^_":
            argument = group(source, i + 1)
            if argument:
                value, end = argument
            elif i + 1 < len(source) and source[i + 1] == "(" and ")" in source[i + 2:]:
                end = source.index(")", i + 2) + 1
                value = source[i + 2:end - 1]
            elif i + 1 < len(source):
                value, end = source[i + 1], i + 2
            else:
                out.append(char)
                i += 1
                continue
            value = unicode_math(value)
            table = SUPERSCRIPT if char == "^" else SUBSCRIPT
            translated = value.translate(table)
            # Do not silently lose unsupported superscripts/subscripts.
            out.append(translated if all(a != b for a, b in zip(value, translated)) else ("⁽" + value + "⁾" if char == "^" else "₍" + value + "₎"))
            i = end
        elif char == "{":
            argument = group(source, i)
            if argument:
                out.append(unicode_math(argument[0]))
                i = argument[1]
            else:
                out.append(char)
                i += 1
        else:
            out.append(" " if char in "\r\n\t" else char)
            i += 1
    return "".join(out)


def cell_width(text: str) -> int:
    """Measure terminal cells, including wide CJK and combining characters."""
    return sum(0 if unicodedata.combining(c) or unicodedata.category(c) in ("Cc", "Cf", "Mn", "Me") else 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def display_math(source: str) -> str:
    """Render a top-level fraction as a readable multi-line terminal fraction."""
    source = source.strip()
    if source.startswith(r"\boxed"):
        argument = group(source, len(r"\boxed"))
        if argument and not source[argument[1]:].strip():
            lines = display_math(argument[0]).splitlines() or [""]
            width = max(map(cell_width, lines))
            return "\n".join(["┌" + "─" * (width + 2) + "┐", *["│ " + line + " " * (width - cell_width(line)) + " │" for line in lines], "└" + "─" * (width + 2) + "┘"])
    # A fraction inside a root or another group must stay inside that group.
    depth = 0
    match = None
    for token in re.finditer(r"\\[a-zA-Z]+|\\.|[{}]", source):
        value = token.group()
        if value == "{":
            depth += 1
        elif value == "}":
            depth = max(0, depth - 1)
        elif depth == 0 and value in (r"\frac", r"\dfrac", r"\tfrac"):
            match = token
            break
    if match:
        numerator = group(source, match.end())
        denominator = group(source, numerator[1]) if numerator else None
        if numerator and denominator:
            prefix = unicode_math(source[:match.start()]).strip()
            suffix = unicode_math(source[denominator[1]:]).strip()
            top = unicode_math(numerator[0]).strip()
            bottom = unicode_math(denominator[0]).strip()
            width = max(cell_width(top), cell_width(bottom), 1)
            lead = prefix + " " if prefix else ""
            def center(text: str) -> str:
                spare = width - cell_width(text)
                return " " * (spare // 2) + text + " " * (spare - spare // 2)
            indent = " " * cell_width(lead)
            return "\n".join((indent + center(top), lead + "─" * width + (" " + suffix if suffix else ""), indent + center(bottom)))
    return unicode_math(source.strip())


# Code fences and spans are consumed first, so examples containing TeX stay literal.
PARTS = re.compile(
    r"(?P<fence>^ {0,3}(?P<marker>`{3,}|~{3,})[^\n]*\n.*?^ {0,3}(?P=marker)[ \t]*(?:\n|$)"
    r"|^ {0,3}(?:`{3,}|~{3,})[^\n]*\n[\s\S]*\Z)"
    r"|(?P<code>`+[^`\n]*`+)"
    r"|(?P<display>\\\[.*?\\\]|\$\$.*?\$\$)"
    r"|(?P<inline>\\\(.*?\\\)|(?<![\\\$])\$(?![\s\$])[^\n$]*?(?<![\s\\])\$(?![\d\$]))"
    r"|(?P<boxed>(?<!\\)\\boxed\s*\{)"
    # Only replace known commands outside delimiters; unknown commands and
    # ordinary Markdown remain untouched.
    r"|(?P<raw_command>(?<!\\)\\(?:" + "|".join(sorted(SYMBOLS, key=len, reverse=True)) + r")(?![a-zA-Z]))"
    r"|(?P<raw_subscript>(?<=[a-zA-Zα-ωΑ-Ω])_\([a-zA-Z0-9+\-=]+\))",
    re.MULTILINE | re.DOTALL,
)


def math_code_block(source: str) -> str:
    # Textual's Markdown fence draws indent guides for ordinary leading spaces.
    # Non-breaking spaces keep the same cell width without spurious │ glyphs.
    lines = display_math(source).split("\n")
    lines = [re.sub(r"^ +", lambda match: "\N{NO-BREAK SPACE}" * len(match.group()), line) for line in lines]
    return "\n\n```lchat-math\n" + "\n".join(lines) + "\n```\n\n"


def render_math_in_markdown(text: str) -> str:
    """Convert math delimiters outside code, preserving other Markdown."""
    output: list[str] = []
    position = 0
    for match in PARTS.finditer(text):
        if match.start() < position:
            continue
        output.append(text[position:match.start()])
        raw = match.group()
        position = match.end()
        if match.group("fence") is not None or match.group("code") is not None:
            output.append(raw)
        elif match.group("boxed") is not None:
            argument = group(text, match.start() + len(r"\boxed"))
            if argument is None:
                output.append(raw)
            else:
                position = argument[1]
                output.append(math_code_block(text[match.start():position]))
        elif match.group("display") is not None:
            output.append(math_code_block(raw[2:-2]))
        elif match.group("raw_command") is not None:
            output.append(SYMBOLS[raw[1:]])
        elif match.group("raw_subscript") is not None:
            value = raw[2:-1]
            translated = value.translate(SUBSCRIPT)
            output.append(translated if all(a != b for a, b in zip(value, translated)) else "₍" + value + "₎")
        else:
            value = unicode_math(raw[2:-2] if raw.startswith("\\(") else raw[1:-1])
            output.append(re.sub(r"([\\`*_{}\[\]<>])", r"\\\1", value))
    output.append(text[position:])
    return "".join(output)
