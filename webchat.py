#!/usr/bin/env python3
"""Browser chat UI for OpenAI-compatible Chat Completions APIs."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "lchat" / "config.json"
DEFAULTS: dict[str, Any] = {
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


class CheckError(Exception):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


def completion_url(endpoint: str) -> str:
    base = api_base(endpoint)
    return base + "/chat/completions"


def api_base(endpoint: str) -> str:
    endpoint = endpoint.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("엔드포인트는 http:// 또는 https:// URL이어야 합니다.")
    if parsed.query or parsed.fragment:
        raise ValueError("엔드포인트 URL에 query string이나 fragment를 넣지 마세요.")
    path = parsed.path.rstrip("/")
    suffix = "/chat/completions"
    if path.endswith(suffix):
        path = path[:-len(suffix)]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def models_url(endpoint: str) -> str:
    return api_base(endpoint) + "/models"


def response_detail(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read().decode("utf-8", errors="replace").strip()
    except OSError:
        body = ""
    if body:
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                detail = parsed.get("error", parsed)
                if isinstance(detail, dict):
                    body = str(detail.get("message") or detail)
                else:
                    body = str(detail)
        except json.JSONDecodeError:
            pass
    return f"HTTP {error.code} {error.reason}" + (f" — {body}" if body else "")


def fetch_models_response(config: dict[str, Any]) -> bytes:
    """Verify the endpoint responds to GET /models and return its body."""
    try:
        url = models_url(config["endpoint"])
    except ValueError as error:
        raise CheckError("endpoint", str(error)) from None
    headers = {"Accept": "application/json"}
    if config.get("api_key"):
        headers["Authorization"] = f"Bearer {config['api_key']}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=float(config["timeout"])) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = response_detail(error)
        if error.code in (401, 403):
            detail += " (API 키/권한을 확인하세요.)"
        elif error.code == 404:
            detail += " (엔드포인트 기본 URL과 /v1 경로를 확인하세요. GET /models가 필요합니다.)"
        raise CheckError("endpoint", detail) from None
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", error)
        if isinstance(reason, (ConnectionRefusedError, socket.gaierror)):
            message = f"연결할 수 없습니다: {reason}. 주소/포트와 모델 서버 실행 상태를 확인하세요."
        elif isinstance(reason, TimeoutError):
            message = f"{config['timeout']}초 안에 응답하지 않았습니다. 엔드포인트와 서버 상태를 확인하세요."
        else:
            message = f"엔드포인트 연결 실패: {reason}"
        raise CheckError("endpoint", message) from None
    except (TimeoutError, OSError) as error:
        raise CheckError("endpoint", f"엔드포인트 연결 실패: {error}") from None


def parse_available_models(raw: bytes) -> list[str]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CheckError("model", f"GET /models 응답이 JSON이 아닙니다: {error}") from None
    if not isinstance(payload, dict):
        raise CheckError("model", "GET /models 응답 형식이 올바르지 않습니다.")
    entries = payload.get("data", payload.get("models"))
    if not isinstance(entries, list):
        raise CheckError("model", "GET /models 응답에 모델 목록(data)이 없습니다. OpenAI 호환 모델 목록 API를 확인하세요.")
    available: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            name = entry
        elif isinstance(entry, dict):
            name = entry.get("id") or entry.get("name")
        else:
            name = None
        if isinstance(name, str) and name and name not in available:
            available.append(name)
    return available


def verify_model_exists(model: str, available: list[str]) -> None:
    if model not in available:
        suggestions = "\n사용 가능한 모델: " + (", ".join(available[:40]) if available else "(목록이 비어 있음)")
        if len(available) > 40:
            suggestions += ", …"
        case_match = next((name for name in available if name.casefold() == model.casefold()), None)
        if case_match:
            suggestions += f"\n대소문자까지 정확히 입력하세요: {case_match}"
        raise CheckError("model", f"모델 '{model}'을 엔드포인트에서 찾지 못했습니다.{suggestions}")


def config_path_from_args(value: str | None) -> Path:
    path = Path(value).expanduser() if value else DEFAULT_CONFIG_PATH
    return path.resolve()


def load_config(path: Path) -> dict[str, Any]:
    config = DEFAULTS.copy()
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"설정 파일을 읽지 못했습니다 ({path}): {error}") from None
        if not isinstance(saved, dict):
            raise ValueError(f"설정 파일 형식이 올바르지 않습니다: {path}")
        config.update({key: value for key, value in saved.items() if key in DEFAULTS})

    env_values: dict[str, tuple[str, Any]] = {
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
                raw = os.environ[env_name]
                config[key] = None if raw == "" and key in ("temperature", "max_tokens") else convert(raw)
    except ValueError as error:
        raise ValueError(f"환경 변수 값이 올바르지 않습니다: {error}") from None
    return config


def save_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    data = {key: config.get(key) for key in DEFAULTS}
    fd, temp_name = tempfile.mkstemp(prefix=".config-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def send_json(handler: BaseHTTPRequestHandler, status: int, data: dict[str, Any]) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def make_handler(state: dict[str, Any]) -> type[BaseHTTPRequestHandler]:
    def server_log(message: str) -> None:
        if not state.get("quiet"):
            print(message, flush=True)

    class Handler(BaseHTTPRequestHandler):
        server_version = "lchat/2.0"

        def config_snapshot(self) -> dict[str, Any]:
            with state["lock"]:
                return state["config"].copy()

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                try:
                    body = (ROOT / "index.html").read_bytes()
                except OSError:
                    send_json(self, 500, {"error": "채팅 페이지 파일을 읽을 수 없습니다."})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/config":
                config = self.config_snapshot()
                send_json(self, 200, {
                    "endpoint": config["endpoint"],
                    "model": config["model"],
                    "prompt_model": bool(state.get("prompt_model", False)),
                    "system": config["system"],
                    "enter_sends": bool(config["enter_sends"]),
                    "tui_enabled": bool(config["tui_enabled"]),
                    "timeout": config["timeout"],
                    "temperature": config["temperature"],
                    "max_tokens": config["max_tokens"],
                    "host": config["host"],
                    "port": config["port"],
                    "web_enabled": config["port"] is not None,
                    "api_key_set": bool(config.get("api_key")),
                    "config_path": str(state["config_path"]),
                })
                return
            send_json(self, 404, {"error": "Not found"})

        def do_POST(self) -> None:
            if self.path == "/api/settings":
                self.update_settings()
                return
            if self.path == "/api/models":
                self.list_models()
                return
            if self.path == "/api/chat":
                self.chat()
                return
            send_json(self, 404, {"error": "Not found"})

        def read_json_body(self) -> dict[str, Any] | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 1_000_000:
                    send_json(self, 413, {"error": "요청 본문은 1바이트~1MB여야 합니다."})
                    return None
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                send_json(self, 400, {"error": f"잘못된 JSON 요청입니다: {error}"})
                return None
            if not isinstance(body, dict):
                send_json(self, 400, {"error": "요청 본문은 JSON 객체여야 합니다."})
                return None
            return body

        def list_models(self) -> None:
            incoming = self.read_json_body()
            if incoming is None:
                return
            candidate = self.config_snapshot()
            endpoint = incoming.get("endpoint", candidate["endpoint"])
            if not isinstance(endpoint, str) or not endpoint.strip():
                send_json(self, 400, {"error": "Endpoint를 입력하세요."})
                return
            candidate["endpoint"] = endpoint.strip()
            if incoming.get("clear_api_key") is True:
                candidate["api_key"] = ""
            elif isinstance(incoming.get("api_key"), str) and incoming["api_key"].strip():
                candidate["api_key"] = incoming["api_key"].strip()
            candidate["timeout"] = min(float(candidate["timeout"]), 8)
            try:
                models = parse_available_models(fetch_models_response(candidate))
            except CheckError as error:
                send_json(self, 502, {"stage": error.stage, "error": str(error)})
                return
            send_json(self, 200, {"models": models})

        def update_settings(self) -> None:
            incoming = self.read_json_body()
            if incoming is None:
                return
            current = self.config_snapshot()
            candidate = current.copy()
            for key in ("endpoint", "model", "system"):
                if key in incoming:
                    if not isinstance(incoming[key], str):
                        send_json(self, 400, {"error": f"{key} 값은 문자열이어야 합니다."})
                        return
                    candidate[key] = incoming[key].strip() if key != "system" else incoming[key]
            if not candidate["endpoint"] or not candidate["model"]:
                send_json(self, 400, {"stage": "settings", "error": "Endpoint와 model을 모두 입력하세요."})
                return
            if len(candidate["system"]) > 20_000:
                send_json(self, 400, {"error": "시스템 프롬프트는 20,000자 이하여야 합니다."})
                return
            if "enter_sends" in incoming:
                if not isinstance(incoming["enter_sends"], bool):
                    send_json(self, 400, {"error": "enter_sends는 true/false여야 합니다."})
                    return
                candidate["enter_sends"] = incoming["enter_sends"]
            if "tui_enabled" in incoming:
                if not isinstance(incoming["tui_enabled"], bool):
                    send_json(self, 400, {"error": "tui_enabled는 true/false여야 합니다."})
                    return
                candidate["tui_enabled"] = incoming["tui_enabled"]
            if "host" in incoming:
                if not isinstance(incoming["host"], str):
                    send_json(self, 400, {"error": "host 값은 문자열이어야 합니다."})
                    return
                candidate["host"] = incoming["host"].strip()
            web_enabled = incoming.get("web_enabled", candidate["port"] is not None)
            if not isinstance(web_enabled, bool):
                send_json(self, 400, {"error": "web_enabled는 true/false여야 합니다."})
                return
            if web_enabled:
                try:
                    candidate["port"] = int(incoming.get("port", candidate["port"] or 8078))
                except (TypeError, ValueError):
                    send_json(self, 400, {"error": "웹 포트는 숫자여야 합니다."})
                    return
                if not candidate["host"] or not 1 <= candidate["port"] <= 65535:
                    send_json(self, 400, {"error": "웹 호스트를 입력하고 포트는 1~65535로 지정하세요."})
                    return
            else:
                candidate["port"] = None
            if "timeout" in incoming:
                try:
                    candidate["timeout"] = float(incoming["timeout"])
                except (TypeError, ValueError):
                    send_json(self, 400, {"error": "timeout은 숫자여야 합니다."})
                    return
            if not 1 <= candidate["timeout"] <= 1800:
                send_json(self, 400, {"error": "timeout은 1~1800초로 지정하세요."})
                return
            try:
                raw_temperature = incoming.get("temperature", candidate["temperature"])
                candidate["temperature"] = None if raw_temperature in (None, "") else float(raw_temperature)
                raw_tokens = incoming.get("max_tokens", candidate["max_tokens"])
                candidate["max_tokens"] = None if raw_tokens in (None, "") else int(raw_tokens)
            except (TypeError, ValueError):
                send_json(self, 400, {"error": "temperature/max_tokens 값이 숫자가 아닙니다."})
                return
            if candidate["temperature"] is not None and not 0 <= candidate["temperature"] <= 2:
                send_json(self, 400, {"error": "temperature는 0~2 범위여야 합니다."})
                return
            if candidate["max_tokens"] is not None and candidate["max_tokens"] < 1:
                send_json(self, 400, {"error": "max_tokens는 1 이상이어야 합니다."})
                return
            if incoming.get("clear_api_key") is True:
                candidate["api_key"] = ""
            elif isinstance(incoming.get("api_key"), str) and incoming["api_key"].strip():
                candidate["api_key"] = incoming["api_key"].strip()

            try:
                server_log(f"[settings] endpoint 확인: {candidate['endpoint']}")
                raw_models = fetch_models_response(candidate)
                server_log("[settings] endpoint 응답 OK")
                models = parse_available_models(raw_models)
                verify_model_exists(candidate["model"], models)
                server_log(f"[settings] model 확인: {candidate['model']} OK")
                with state["lock"]:
                    save_config(state["config_path"], candidate)
                    state["config"] = candidate
            except CheckError as error:
                server_log(f"[settings] {error.stage} 확인 실패: {error}")
                send_json(self, 502, {"stage": error.stage, "error": str(error)})
                return
            except OSError as error:
                send_json(self, 500, {"stage": "config", "error": f"설정 파일 저장 실패: {error}"})
                return
            send_json(self, 200, {
                "ok": True,
                "model": candidate["model"],
                "models": models,
                "config_path": str(state["config_path"]),
                "host": candidate["host"],
                "port": candidate["port"],
                "web_enabled": candidate["port"] is not None,
                "enter_sends": candidate["enter_sends"],
                "tui_enabled": candidate["tui_enabled"],
                "restart_required": candidate["host"] != current["host"] or candidate["port"] != current["port"] or candidate["tui_enabled"] != current["tui_enabled"],
                "message": "Endpoint와 모델을 확인하고 설정을 저장했습니다.",
            })

        def chat(self) -> None:
            incoming = self.read_json_body()
            if incoming is None:
                return
            turns = incoming.get("messages")
            if not isinstance(turns, list) or not turns:
                send_json(self, 400, {"error": "messages 목록이 필요합니다."})
                return
            config = self.config_snapshot()
            messages = [{"role": "system", "content": config["system"]}]
            for turn in turns[-100:]:
                if not isinstance(turn, dict) or turn.get("role") not in ("user", "assistant"):
                    send_json(self, 400, {"error": "메시지 role은 user 또는 assistant여야 합니다."})
                    return
                if not isinstance(turn.get("content"), str):
                    send_json(self, 400, {"error": "메시지 내용은 문자열이어야 합니다."})
                    return
                messages.append({"role": turn["role"], "content": turn["content"]})
            payload: dict[str, Any] = {"model": config["model"], "messages": messages, "stream": True, "stream_options": {"include_usage": True}}
            if config["temperature"] is not None:
                payload["temperature"] = config["temperature"]
            if config["max_tokens"] is not None:
                payload["max_tokens"] = config["max_tokens"]
            headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
            if config.get("api_key"):
                headers["Authorization"] = f"Bearer {config['api_key']}"
            upstream_url = completion_url(config["endpoint"])
            def open_upstream() -> Any:
                request = urllib.request.Request(
                    upstream_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                try:
                    return urllib.request.urlopen(request, timeout=float(config["timeout"]))
                except urllib.error.HTTPError as error:
                    if payload.get("stream_options") and error.code in (400, 422):
                        error.close()
                        payload.pop("stream_options", None)
                        request = urllib.request.Request(
                            upstream_url,
                            data=json.dumps(payload).encode("utf-8"),
                            headers=headers,
                            method="POST",
                        )
                        return urllib.request.urlopen(request, timeout=float(config["timeout"]))
                    raise
            try:
                upstream = open_upstream()
            except urllib.error.HTTPError as error:
                body = error.read()
                try:
                    self.send_response(error.code)
                    self.send_header("Content-Type", error.headers.get("Content-Type", "application/json"))
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                reason = getattr(error, "reason", error)
                try:
                    send_json(self, 502, {"error": f"모델 API 연결 실패: {reason}"})
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return

            with upstream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    for line in upstream:
                        self.wfile.write(line)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError) as error:
                    server_log(f"[chat] stream 중단: {error}")

        def log_message(self, fmt: str, *args: Any) -> None:
            server_log(f"[{self.log_date_time_string()}] {fmt % args}")

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the lchat full-window browser interface.")
    parser.add_argument("--endpoint", help="OpenAI-compatible API base URL; env: OPENAI_BASE_URL")
    parser.add_argument("--model", help="Exact model ID; env: OPENAI_MODEL")
    parser.add_argument("--api-key", help="API key; env: OPENAI_API_KEY")
    parser.add_argument("--system", help="System prompt; env: LCHAT_SYSTEM_PROMPT")
    parser.add_argument("--host", help="Listen address; env: LCHAT_HOST (use 0.0.0.0 for LAN access)")
    parser.add_argument("--port", type=int, help="Enable browser hosting on this port; env: LCHAT_PORT")
    parser.add_argument("--timeout", type=float, help="Upstream timeout in seconds; env: LCHAT_TIMEOUT")
    parser.add_argument("--temperature", type=float, help="Sampling temperature; env: LCHAT_TEMPERATURE")
    parser.add_argument("--max-tokens", type=int, help="Maximum generated tokens; env: LCHAT_MAX_TOKENS")
    parser.add_argument("--no-stream", action="store_true", help="Disable streaming in the terminal UI")
    parser.add_argument("--config", help=f"JSON settings file (default: {DEFAULT_CONFIG_PATH})")
    parser.add_argument("--cfg", action="store_true", help="Print the absolute settings-file path and exit")
    parser.add_argument("--reset", action="store_true", help="Reset settings to defaults before running")
    ui_mode = parser.add_mutually_exclusive_group()
    ui_mode.add_argument("--tui", action="store_true", help="Run the terminal UI too (with --port, alongside the web UI)")
    ui_mode.add_argument("--no-tui", action="store_true", help="Disable the terminal UI; use web-only when hosting")
    args = parser.parse_args()
    path = config_path_from_args(args.config)
    if args.cfg:
        print(path)
        return 0
    if args.reset:
        try:
            save_config(path, DEFAULTS)
        except OSError as error:
            parser.error(f"Could not reset config file {path}: {error}")
        print(f"설정을 기본값으로 초기화했습니다: {path}", flush=True)
    try:
        config = load_config(path)
    except ValueError as error:
        parser.error(str(error))
    if not isinstance(config.get("enter_sends"), bool):
        parser.error("설정 파일의 enter_sends는 true/false여야 합니다.")
    if not isinstance(config.get("tui_enabled"), bool):
        parser.error("설정 파일의 tui_enabled는 true/false여야 합니다.")
    launch_tui = args.tui or (config["tui_enabled"] and not args.no_tui)
    for key in ("endpoint", "model", "api_key", "system", "host", "port", "timeout", "temperature", "max_tokens"):
        value = getattr(args, key)
        if value is not None:
            config[key] = value
    hosting_requested = args.port is not None or config.get("port") is not None

    def terminal_command(force_tui: bool | None = None) -> tuple[list[str], dict[str, str]]:
        command = [
            sys.executable,
            str(ROOT / "lchat.py"),
            "--config", str(path),
            "--endpoint", str(config["endpoint"]),
            "--system", str(config["system"]),
            "--timeout", str(config["timeout"]),
            "--terminal-only",
        ]
        if args.model is not None:
            command.extend(("--model", args.model))
        if args.no_stream:
            command.append("--no-stream")
        if config.get("temperature") not in (None, ""):
            command.extend(("--temperature", str(config["temperature"])))
        if config.get("max_tokens") not in (None, ""):
            command.extend(("--max-tokens", str(config["max_tokens"])))
        if force_tui is True:
            command.append("--tui")
        elif force_tui is False:
            command.append("--plain")
        child_env = os.environ.copy()
        if config.get("api_key"):
            child_env["OPENAI_API_KEY"] = str(config["api_key"])
        if config.get("port") is not None:
            child_env["LCHAT_PORT"] = str(config["port"])
            child_env["LCHAT_HOST"] = str(config["host"])
        return command, child_env

    if not hosting_requested:
        if not isinstance(config["endpoint"], str) or not config["endpoint"].strip():
            parser.error("터미널 대화에는 --endpoint/OPENAI_BASE_URL 또는 설정 파일의 endpoint가 필요합니다.")
        force_tui = True if args.tui else (False if args.no_tui or not config["tui_enabled"] else None)
        command, child_env = terminal_command(force_tui)
        print("웹 호스팅 없이 터미널 대화를 시작합니다.", flush=True)
        return subprocess.call(command, env=child_env)

    if not isinstance(config["endpoint"], str) or not config["endpoint"].strip():
        parser.error("--endpoint 또는 OPENAI_BASE_URL/config.json의 endpoint가 필요합니다.")
    if not isinstance(config["model"], str):
        parser.error("설정 파일의 model은 문자열이어야 합니다.")
    if not isinstance(config["host"], str) or not config["host"].strip():
        parser.error("--host 또는 LCHAT_HOST는 비어 있을 수 없습니다.")
    try:
        config["port"] = int(args.port if args.port is not None else config["port"])
        config["timeout"] = float(config["timeout"])
        config["temperature"] = None if config["temperature"] in (None, "") else float(config["temperature"])
        config["max_tokens"] = None if config["max_tokens"] in (None, "") else int(config["max_tokens"])
    except (TypeError, ValueError):
        parser.error("config.json 또는 환경 변수의 port/timeout/temperature/max_tokens 값이 올바르지 않습니다.")
    if not 1 <= config["port"] <= 65535:
        parser.error("--port는 1~65535 범위여야 합니다.")
    if not 1 <= config["timeout"] <= 1800:
        parser.error("--timeout은 1~1800초 범위여야 합니다.")
    if config["temperature"] is not None and not 0 <= config["temperature"] <= 2:
        parser.error("--temperature는 0~2 범위여야 합니다.")
    if config["max_tokens"] is not None and config["max_tokens"] < 1:
        parser.error("--max-tokens는 1 이상이어야 합니다.")
    try:
        completion_url(config["endpoint"])
    except ValueError as error:
        parser.error(str(error))

    print("[1/3] Endpoint 접속 및 모델 목록 확인 중…", flush=True)
    try:
        check_config = {**config, "timeout": min(config["timeout"], 8)}
        available = parse_available_models(fetch_models_response(check_config))
        if config["model"] in available:
            print(f"[완료] 모델 확인됨 (API 목록 {len(available)}개).", flush=True)
        else:
            print("[안내] 지정한 모델이 없습니다. UI에서 모델 목록을 확인하고 선택하세요.", flush=True)
    except CheckError as error:
        print(f"[경고: {error.stage}] {error} · UI 설정에서 다시 확인하세요.", flush=True)
    print(f"[3/3] {config['host']}:{config['port']} 바인딩 확인 중…", flush=True)
    has_tty = sys.stdin.isatty() and sys.stdout.isatty()
    if args.tui and not has_tty:
        parser.error("--tui를 사용하려면 대화형 터미널이 필요합니다.")
    run_tui = launch_tui and has_tty
    state: dict[str, Any] = {"config": config, "config_path": path, "lock": threading.RLock(), "quiet": run_tui, "prompt_model": not bool(args.model and args.model.strip())}
    try:
        server = ThreadingHTTPServer((config["host"], config["port"]), make_handler(state))
    except OSError as error:
        if error.errno in (48, 98, 10048):
            print(f"[실패: port] {config['host']}:{config['port']} 포트가 이미 사용 중입니다. 다른 --port를 지정하세요. ({error})", flush=True)
        else:
            print(f"[실패: bind] {config['host']}:{config['port']} 바인딩 실패: {error}", flush=True)
        return 3
    print("[완료] 포트 바인딩 성공.", flush=True)
    print(f"\nlchat 웹 UI: http://{config['host']}:{config['port']}/", flush=True)
    print(f"설정 파일: {path}", flush=True)
    if run_tui:
        print("TUI와 웹 UI를 함께 실행합니다. Ctrl+C로 종료합니다.", flush=True)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        command, child_env = terminal_command(True)
        try:
            return subprocess.call(command, env=child_env)
        except KeyboardInterrupt:
            return 130
        finally:
            server.shutdown()
            server_thread.join(timeout=2)
            server.server_close()
    print("웹 서버를 실행 중입니다. Ctrl+C로 종료합니다. 외부 공개 시 접근을 제한하세요.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
