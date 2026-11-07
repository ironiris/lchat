# lchat

**v0.3.0** · OpenAI 호환 API를 사용하는 가벼운 터미널 채팅 클라이언트입니다. 로컬 LLM의 응답을 확인하는 용도로 만들었으며, Textual 기반 전체 화면 TUI와 선택적인 웹 UI를 제공합니다.

## 요구사항

- Python 3.9 이상, Textual 2.1.2 이상 3.0 미만. 설치 스크립트는 선택한 Python에 Textual을 설치합니다. Linux/macOS 설치에는 Git과 curl도 필요합니다.
- TUI에는 대화형 터미널이 필요합니다.
- `GET /models`와 `POST /chat/completions`를 지원하는 OpenAI 호환 모델 서버

## 시작하기

설치 스크립트를 내려받아 실행하세요.

```bash
installer=$(mktemp)
curl -fsSL https://raw.githubusercontent.com/ironiris/lchat/main/install.sh -o "$installer" && sh "$installer"
rm -f "$installer"
lchat
```

스크립트가 Python·필수 표준 라이브러리·Git을 확인하고 Textual을 사용자 Python에 설치한 뒤, 프로그램을 `~/.local/share/lchat`에 내려받고 `~/.local/bin/lchat` 링크를 만듭니다. 시스템에서 사용자 pip 설치를 제한하는 경우에는 OS 패키지 관리자 등을 통해 Textual을 설치해야 합니다. 누락된 시스템 의존성은 자동으로 설치하지 않고 안내합니다. 다른 파일이나 링크가 이미 있으면 덮어쓰지 않습니다. `~/.local/bin`이 PATH에 없으면 스크립트가 설정 방법을 안내합니다.

업데이트는 `git -C ~/.local/share/lchat pull --ff-only`로 할 수 있습니다. 의존성이 변경된 경우 요구 버전의 Textual도 설치하세요. pip 사용자 설치가 허용되는 환경에서는 `python3 -m pip install --user -r ~/.local/share/lchat/requirements.txt`를 사용할 수 있습니다. 설치 스크립트를 다시 실행하면 기존 설치의 소스는 수정하지 않습니다. 삭제하려면 `rm ~/.local/bin/lchat`과 `rm -rf ~/.local/share/lchat`을 실행하세요. 설정 파일은 별도로 유지됩니다.

설치 및 명령 디렉터리는 각각 `LCHAT_INSTALL_DIR`, `LCHAT_BIN_DIR` 환경 변수로 변경할 수 있습니다. 스크립트 없이 직접 `git clone`한 뒤 Textual을 설치하고 `python3 lchat.py`로 실행해도 됩니다. pip 사용자 설치가 허용된 환경에서는 `python3 -m pip install --user -r requirements.txt`를 사용하세요. Debian 등에서 `externally-managed-environment` 오류가 나면 OS 패키지 관리자에서 요구 버전의 Textual을 설치하세요. 예: `sudo apt install python3-textual` (패키지 버전이 2.1.2 이상 3.0 미만이어야 함). 이미 해당 버전이 설치되어 있다면 추가 설치는 필요 없습니다.

### Windows 설치 (웹 호스팅 미지원)

PowerShell에서 설치 스크립트를 내려받아 실행하세요. 선택한 Python 3.9+에 `pip`로 Textual을 설치하고, 앱 파일을 `%LOCALAPPDATA%\lchat`에 내려받습니다. 설치 경로가 이미 있으면 앱 파일을 자동 업데이트하며 설정 파일은 유지합니다. 웹 호스팅은 실행하지 않습니다.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
$installer = Join-Path $env:TEMP "lchat-install.ps1"
Invoke-WebRequest https://raw.githubusercontent.com/ironiris/lchat/v0.3.0/install.ps1 -OutFile $installer -UseBasicParsing
& $installer
```

특정 Python 환경에 설치하려면 먼저 해당 인터프리터를 지정하세요. 모듈은 그 Python에 설치되고, 이후에도 같은 인터프리터로 앱을 실행해야 합니다.

```powershell
$env:LCHAT_PYTHON = "C:\path\to\python.exe"
$installer = Join-Path $env:TEMP "lchat-install.ps1"
Invoke-WebRequest https://raw.githubusercontent.com/ironiris/lchat/v0.3.0/install.ps1 -OutFile $installer -UseBasicParsing
& $installer
.\lchat.bat
```

설치 스크립트는 실행한 PowerShell 폴더에 `lchat.bat`을 만듭니다. 그 파일이 이미 있으면 `lchat-2.bat`처럼 다른 이름을 사용합니다. 설치 후 `.\lchat.bat`을 실행하세요. 배치 파일은 웹서빙 없이 설치 당시 선택한 Python으로 TUI를 실행합니다.

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
| Ctrl+J | 입력 중 줄바꿈 (터미널에서 구분 지원 시) |
| ↑ / ↓ | 입력 커서 이동, 경계에서 입력 기록 탐색 |
| PageUp / PageDown, 마우스 휠 | 대화 스크롤 |
| F2 | 환경 설정 열기 |
| F3 | 마지막 응답의 생각 펼치기/접기 |
| F4 | 서버 모델 목록 열기 |
| Ctrl+L | 화면 다시 그리기 |
| Ctrl+U | 입력 지우기 |
| Ctrl+Q / Ctrl+D | 종료 |
| 마우스 드래그, Ctrl+C | 대화 내용 선택 및 복사 |
| `/help`, `/reset`, `/exit` | 도움말, 대화 기록 지우기, 종료 |

설정에서 Enter를 줄바꿈으로 바꾸면 Ctrl+S 또는 지원되는 터미널에서 Ctrl+Enter로 전송합니다. 사용자 메시지는 배경색과 함께 오른쪽에 표시되며, 긴 입력은 줄바꿈됩니다. 응답 중에는 최신 내용을 자동으로 따라갑니다. 이전 내용을 보기 위해 위로 스크롤하면 자동 이동이 멈추며, 맨 아래로 돌아가면 다시 따라갑니다.

대화 내용은 마우스로 드래그해 선택하고 Ctrl+C로 복사합니다. 복사는 터미널의 OSC 52 클립보드 지원과 설정에 따라 동작하며, macOS 기본 터미널 등에서 지원하지 않는 경우 터미널의 자체 텍스트 선택 기능을 이용하세요. 응답 생성 중에는 새 내용이 표시되면서 선택이 해제될 수 있습니다.

F2 설정의 **System prompt**는 여러 줄로 편집할 수 있습니다. 설정창의 **저장**을 눌러 파일에 반영하세요. 화면이 깨지면 Ctrl+L을 눌러 보세요.

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

TUI의 F2 또는 웹 UI의 설정 버튼에서 endpoint, 모델 ID, API 키, 시스템 프롬프트, timeout, temperature, 최대 생성 토큰, Qwen 추론 켜기/끄기, 입력 키 동작, TUI 및 웹 호스팅 설정을 변경할 수 있습니다. 저장 전에는 endpoint와 모델 ID를 확인합니다. UI 실행 방식 및 웹 바인딩 변경은 다음 실행부터 적용됩니다.

**Qwen 추론** 설정은 모델 ID에 `qwen`이 포함된 경우 요청에 `enable_thinking: true` 또는 `false`를 명시적으로 보냅니다. 모델 서버와 해당 모델이 이 옵션을 지원해야 적용됩니다. 설정 파일 키는 `disable_qwen_thinking`이며 `LCHAT_DISABLE_QWEN_THINKING=true` 환경 변수로 추론을 끌 수 있습니다.

설정 파일은 기본적으로 `~/.config/lchat/config.json`에 저장됩니다. `XDG_CONFIG_HOME`이 있으면 그 아래의 `lchat/config.json`을 사용합니다. API 키는 설정 파일에 저장될 수 있으므로 파일 권한을 `600`으로 제한합니다. 설정 파일을 공유하거나 Git에 올리지 마세요.

```bash
python3 lchat.py --config                  # 기본 설정 파일의 절대 경로 출력 후 종료
python3 lchat.py --config /path/config.json # 지정한 설정 파일 사용
python3 lchat.py --reset                   # 기본값으로 초기화 후 실행
```

초기화하면 로컬 기본 endpoint, 모델 미선택, TUI 켜짐, 웹 호스팅 꺼짐 상태가 됩니다. 명령행 옵션은 환경 변수 및 설정 파일보다 우선합니다. 환경 변수로도 설정할 수 있습니다.

```bash
export OPENAI_BASE_URL="http://localhost:8070/v1"
export OPENAI_MODEL="your-model-id"
export OPENAI_API_KEY="your-api-key"  # 필요한 경우에만
```

그 밖에 `LCHAT_SYSTEM_PROMPT`, `LCHAT_HOST`, `LCHAT_PORT`, `LCHAT_TIMEOUT`, `LCHAT_TEMPERATURE`, `LCHAT_MAX_TOKENS`, `LCHAT_DISABLE_QWEN_THINKING`을 지원합니다.

## 응답 표시와 측정

응답 중에는 연결 대기, 추론 및 출력 상태를 표시합니다. `<thinking>` 태그나 서버의 reasoning 필드는 기본적으로 접어 두며 TUI의 F3 또는 웹 UI에서 펼칠 수 있습니다. 완료 후 첫 출력까지의 시간, 생성 시간, tok/s와 전체 시간을 표시합니다. TUI는 Textual Markdown 위젯으로 제목, 강조, 코드 블록, 표 등을 표시합니다. `\[...\]`, `$$...$$`, `\(...\)` 및 `$...$` 수식에서 분수, 제곱근, 위첨자와 자주 쓰는 기호를 별도 패키지 없이 유니코드로 표시합니다. 완전한 LaTeX 엔진은 아니며 지원하지 않는 명령은 원문을 유지합니다.

서버가 `usage.completion_tokens`를 제공하면 해당 토큰 수로 속도를 계산합니다. 제공하지 않으면 텍스트 길이 기반 추정치를 `~`로 표시합니다. 시간은 클라이언트에서 측정하므로 서버 내부 추론 시간과 다를 수 있습니다.

lchat은 모델 서버에 요청을 보내는 클라이언트입니다. GPU 선택, VRAM 할당 및 CPU/GPU 오프로딩은 모델 서버에서 설정해야 합니다.

## 응답 디버그 로그

모델이 실제로 보낸 내용을 확인하려면 로그 옵션을 사용하세요. 기본적으로 로그는 저장하지 않습니다.

```bash
lchat --log
# 웹 UI 포함 실행에도 사용 가능
lchat --port 8078 --log
```

명령을 실행한 디렉터리의 `log/`에 요청마다 별도 파일이 생성됩니다.

- `*.sse`: 스트리밍 API의 원본 이벤트 (`data:`, JSON, `[DONE]` 포함)
- `*.json`: `--no-stream`일 때 원본 JSON 응답
- `*.answer.txt`: 스트리밍 조각을 합친 답변 원문. 화면의 수식 변환은 적용하지 않습니다.
- `*.thinking.txt`: 서버가 별도 reasoning 필드를 제공한 경우 그 내용. `<think>` 등의 태그가 본문에 포함된 응답은 `answer.txt`에 그대로 기록됩니다.

웹 UI와 터미널 UI를 함께 실행하면 양쪽 응답이 각각 기록됩니다. 원본 이벤트는 서버가 보낸 JSON 표기 그대로이므로 `\\n`, `\\u` 같은 이스케이프가 있을 수 있습니다. API 키나 요청 헤더, 사용자 요청은 별도로 기록하지 않지만, 서버 응답 자체에 대화 내용 등 민감한 정보가 포함될 수 있습니다. 파일은 사용자만 읽고 쓸 수 있도록 생성하며(OS 지원 범위 내), 공유하거나 공개 저장소에 올리기 전에 확인하세요. 저장소 루트의 `log/`는 Git에서 제외됩니다.

## 파일 구성

- `install.sh`: 의존성 확인, 프로그램 다운로드 및 실행 링크 설치
- `lchat.py`: 주 실행 파일, API 클라이언트와 줄 단위 UI
- `lchat_tui.py`: Textual 전체 화면 UI
- `lchat_math.py`: 터미널 수식 변환
- `requirements.txt`: Textual 의존성
- `lchat_web.py`: 웹 서버 및 설정 검증
- `index.html`: 웹 UI

이전 버전의 `webchat.py` 호환 진입점은 v0.1.1에서 제거되었습니다. 대신 `lchat.py`를 실행하세요.
