# lchat

**v0.1.1** · OpenAI 호환 API를 사용하는 가벼운 터미널 채팅 클라이언트입니다. 로컬 LLM의 응답을 확인하는 용도로 만들었으며, ncurses TUI와 선택적인 웹 UI를 제공합니다. Python 표준 라이브러리만 사용합니다.

## 요구사항

- Python 3.9 이상, Git, curl. 설치 시 Python `curses` 및 표준 라이브러리를 확인합니다.
- TUI에는 대화형 터미널이 필요합니다.
- `GET /models`와 `POST /chat/completions`를 지원하는 OpenAI 호환 모델 서버
- 별도 패키지 설치 불필요

## 시작하기

설치 스크립트를 내려받아 실행하세요.

```bash
installer=$(mktemp)
curl -fsSL https://raw.githubusercontent.com/ironiris/lchat/main/install.sh -o "$installer" && sh "$installer"
rm -f "$installer"
lchat
```

스크립트가 Python·필수 표준 라이브러리·Git을 확인하고, 프로그램을 `~/.local/share/lchat`에 내려받은 뒤 `~/.local/bin/lchat` 링크를 만듭니다. root 권한이나 추가 Python 패키지는 필요하지 않습니다. 누락된 시스템 의존성은 자동으로 설치하지 않고 안내합니다. 다른 파일이나 링크가 이미 있으면 덮어쓰지 않습니다. `~/.local/bin`이 PATH에 없으면 스크립트가 설정 방법을 안내합니다.

업데이트는 `git -C ~/.local/share/lchat pull --ff-only`로 할 수 있습니다. 설치 스크립트를 다시 실행하면 기존 설치는 수정하지 않습니다. 삭제하려면 `rm ~/.local/bin/lchat`과 `rm -rf ~/.local/share/lchat`을 실행하세요. 설정 파일은 별도로 유지됩니다.

설치 및 명령 디렉터리는 각각 `LCHAT_INSTALL_DIR`, `LCHAT_BIN_DIR` 환경 변수로 변경할 수 있습니다. 스크립트 없이 직접 `git clone`한 뒤 `python3 lchat.py`로 실행해도 됩니다.

기본 endpoint는 `http://localhost:8070/v1`이며 기본 모델은 지정되어 있지 않습니다. TUI에서 서버의 모델 목록을 확인해 선택하거나 직접 지정할 수 있습니다.

```bash
python3 lchat.py --endpoint http://localhost:8070/v1 --model Qwen3.8FN
python3 lchat.py --version
python3 lchat.py --help
```

`Qwen3.8FN`은 예시입니다. 모델 ID는 서버의 `/models` 목록에 있는 이름을 사용하세요. endpoint는 API 기본 URL 또는 전체 `/chat/completions` URL을 사용할 수 있습니다.

`--model`을 생략하거나 지정된 모델이 서버 목록에 없으면 TUI에 모델 선택 팝업이 열립니다. 저장된 모델이 목록에 있으면 기본 선택되므로 Enter로 확인할 수 있습니다. 팝업의 선택은 현재 실행에 적용됩니다. 다음 실행에도 유지하려면 F2 설정에서 저장하세요.

## 터미널 사용법

대화형 터미널에서는 기본적으로 전체 화면 TUI가 열립니다. `--tui`로 TUI를 강제하거나 `--plain`으로 줄 단위 모드를 사용할 수 있습니다.

| 키 / 명령 | 동작 |
| --- | --- |
| Enter | 메시지 전송 (기본값) |
| Ctrl+J | 입력 중 줄바꿈 (기본값) |
| ↑ / ↓ | 입력 커서 이동, 경계에서 입력 기록 탐색 |
| PageUp / PageDown, 마우스 휠 | 대화 스크롤 |
| F2 | 환경 설정 열기 |
| F3 | 마지막 응답의 생각 펼치기/접기 |
| F4 | 서버 모델 목록 열기 |
| Ctrl+L | 화면 다시 그리기 |
| Ctrl+U | 입력 지우기 |
| `/help`, `/reset`, `/exit` | 도움말, 대화 기록 지우기, 종료 |

설정에서 Enter를 줄바꿈으로 바꾸면 Ctrl+Enter로 전송합니다. Ctrl+Enter를 구분하지 못하는 터미널에서는 Ctrl+S를 사용할 수 있습니다. 사용자 메시지는 오른쪽에 표시되며, 긴 입력은 줄바꿈됩니다.

F2 설정의 **System prompt**에서 Enter를 누르면 여러 줄 편집 창이 열립니다. Enter는 줄바꿈, Ctrl+S 또는 F2는 적용, Esc는 취소입니다. 편집 창에서 적용한 뒤 설정창에서도 **저장**해야 파일에 반영됩니다.

터미널의 포커스 이벤트를 지원하면 돌아올 때 화면을 다시 그립니다. 터미널 크기도 확인하며 Herdr 안에서는 재접속 복원을 위해 주기적으로 화면을 새로 그립니다. 화면이 깨지면 Ctrl+L을 눌러 보세요.

## 웹 UI

웹 호스팅은 기본적으로 꺼져 있습니다. `--port`를 지정하면 웹 UI를 **추가로** 실행하며, 대화형 터미널에서 TUI 실행 설정이 켜져 있으면 TUI와 함께 실행됩니다.

```bash
python3 lchat.py --port 8078 --tui     # TUI + 웹 UI
python3 lchat.py --port 8078 --no-tui  # 웹 UI만
```

기본 바인딩 주소는 `127.0.0.1`입니다. 실행 후 같은 컴퓨터의 브라우저에서 `http://127.0.0.1:8078/`을 열어 주세요. 저장된 설정에 포트가 있으면 `--port` 없이 실행해도 웹 호스팅이 활성화됩니다. 터미널이 없는 환경에서는 웹 전용으로 실행합니다. TUI와 웹 UI를 함께 띄운 경우 TUI를 종료하면 웹 서버도 종료되며, 각 UI의 대화 기록은 별개입니다.

**보안 주의:** 웹 UI에는 사용자 인증이 없습니다. 웹 UI를 사용할 수 있는 사람은 채팅 요청을 보내고 설정을 변경할 수 있습니다. 기본 `127.0.0.1` 바인딩을 유지하고, `--host 0.0.0.0` 등으로 외부에 공개해야 한다면 신뢰할 수 있는 네트워크와 방화벽 또는 별도의 인증 계층으로 접근을 제한하세요.

시작 시 모델 서버와 모델 목록을 확인합니다. 연결이 실패하거나 모델이 없으면 경고 후 UI에서 설정을 수정할 수 있습니다. 웹 포트 바인딩이 실패하면 실행을 중단합니다. 웹 설정에서도 모델 목록을 불러와 선택할 수 있습니다.

## 설정

TUI의 F2 또는 웹 UI의 설정 버튼에서 endpoint, 모델 ID, API 키, 시스템 프롬프트, timeout, temperature, 최대 생성 토큰, 입력 키 동작, TUI 및 웹 호스팅 설정을 변경할 수 있습니다. 저장 전에는 endpoint와 모델 ID를 확인합니다. UI 실행 방식 및 웹 바인딩 변경은 다음 실행부터 적용됩니다.

설정 파일은 기본적으로 `~/.config/lchat/config.json`에 저장됩니다. `XDG_CONFIG_HOME`이 있으면 그 아래의 `lchat/config.json`을 사용합니다. API 키는 설정 파일에 저장될 수 있으므로 파일 권한을 `600`으로 제한합니다. 설정 파일을 공유하거나 Git에 올리지 마세요.

```bash
python3 lchat.py --cfg                      # 설정 파일의 절대 경로 출력
python3 lchat.py --config /path/config.json # 다른 설정 파일 사용
python3 lchat.py --reset                    # 기본값으로 초기화 후 실행
```

초기화하면 로컬 기본 endpoint, 모델 미선택, TUI 켜짐, 웹 호스팅 꺼짐 상태가 됩니다. 명령행 옵션은 환경 변수 및 설정 파일보다 우선합니다. 환경 변수로도 설정할 수 있습니다.

```bash
export OPENAI_BASE_URL="http://localhost:8070/v1"
export OPENAI_MODEL="your-model-id"
export OPENAI_API_KEY="your-api-key"  # 필요한 경우에만
```

그 밖에 `LCHAT_SYSTEM_PROMPT`, `LCHAT_HOST`, `LCHAT_PORT`, `LCHAT_TIMEOUT`, `LCHAT_TEMPERATURE`, `LCHAT_MAX_TOKENS`를 지원합니다.

## 응답 표시와 측정

응답 중에는 연결 대기, 추론 및 출력 상태를 표시합니다. `<thinking>` 태그나 서버의 reasoning 필드는 기본적으로 접어 두며 TUI의 표시 줄/F3 또는 웹 UI에서 펼칠 수 있습니다. 완료 후 첫 출력까지의 시간, 생성 시간, tok/s와 전체 시간을 표시합니다.

서버가 `usage.completion_tokens`를 제공하면 해당 토큰 수로 속도를 계산합니다. 제공하지 않으면 텍스트 길이 기반 추정치를 `~`로 표시합니다. 시간은 클라이언트에서 측정하므로 서버 내부 추론 시간과 다를 수 있습니다.

lchat은 모델 서버에 요청을 보내는 클라이언트입니다. GPU 선택, VRAM 할당 및 CPU/GPU 오프로딩은 모델 서버에서 설정해야 합니다.

## 파일 구성

- `install.sh`: 의존성 확인, 프로그램 다운로드 및 실행 링크 설치
- `lchat.py`: 주 실행 파일과 터미널 UI
- `lchat_web.py`: 웹 서버 및 설정 검증
- `index.html`: 웹 UI

이전 버전의 `webchat.py` 호환 진입점은 v0.1.1에서 제거되었습니다. 대신 `lchat.py`를 실행하세요.
