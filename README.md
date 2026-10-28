# lchat

버전: 0.1.0

OpenAI 호환 Chat Completions API를 사용하는 텍스트 챗봇입니다. 브라우저용 전체 창 UI와 간단한 터미널 UI를 제공합니다. Python 표준 라이브러리만 사용합니다.

## 브라우저 UI 실행

```bash
cd ~/lchat
python3 webchat.py --port 8078 --host 0.0.0.0 \
  --endpoint http://localhost:8070/v1 \
  --model Qwen3.8FN
```

시작 시 endpoint의 `GET /models`와 모델 ID를 확인한 다음 host/port에 바인딩합니다. 모델이 없거나 서버에 연결할 수 없으면 경고 후 UI를 실행해 설정을 고칠 수 있습니다. 포트 바인딩에 실패하면 실행을 중단합니다. 정상 실행 후 `http://서버IP:8078`에서 사용할 수 있습니다. 같은 PC에서만 쓸 때는 `--host 127.0.0.1`을 사용하세요.

`webchat.py` 또는 `lchat.py`에 `--port`를 지정하면 웹 UI를 실행합니다. 대화형 터미널에서는 설정의 **TUI 실행**이 켜져 있으면 TUI도 함께 뜹니다. `--no-tui`로 웹 전용 실행을 강제하거나, `--tui`로 TUI를 강제할 수 있습니다. 설정 파일의 `port`와 `tui_enabled`도 실행 방식을 결정합니다. 두 UI 실행 여부는 TUI 설정창 또는 웹 설정 팝업에서 각각 바꿀 수 있습니다. `--port`와 설정 파일의 `port`가 모두 없으면 웹 서버 없이 터미널 대화를 실행합니다. 터미널이 없는 환경에서는 웹 전용으로 실행합니다 (`--tui`는 터미널 필요). TUI를 종료하면 함께 띄운 웹 서버도 종료됩니다. 각 UI의 대화 기록은 별개입니다.

```bash
python3 lchat.py --port 8078 --tui     # TUI + 웹 UI
python3 lchat.py --port 8078 --no-tui  # 웹 UI만
python3 lchat.py --plain               # 웹 없이 줄 단위 터미널 UI
```

환경 설정 파일의 전체 경로만 확인하려면 다음처럼 실행하세요. 파일이 아직 없어도 경로만 출력하고 종료합니다.

```bash
python3 webchat.py --cfg
# 사용자 지정 설정 파일을 쓸 때
python3 webchat.py --config /path/to/config.json --cfg
# 터미널 챗봇에서도 동일
python3 lchat.py --cfg
```

### 환경 설정 팝업 및 저장

상단의 **설정**에서 endpoint, 모델 ID, API 키, 시스템 프롬프트, timeout, temperature, 최대 토큰을 수정할 수 있습니다. `--model`을 생략하거나 지정한 모델이 서버 목록에 없으면 웹 설정이 자동으로 열립니다. 저장된 모델이 목록에 있으면 미리 선택되며 Enter로 확인할 수 있습니다. **서버 모델 목록 불러오기**로 목록을 갱신하고, **검사 후 저장**을 누르면 endpoint와 모델을 다시 확인한 뒤 저장합니다.

기본 설정 파일 경로:

```text
~/.config/lchat/config.json
```

`XDG_CONFIG_HOME`이 설정되어 있으면 그 디렉터리 아래 `lchat/config.json`을 사용합니다. 다른 경로는 `--config /경로/config.json`으로 지정할 수 있습니다. 저장되는 API 키는 설정 파일 권한을 `600`으로 제한합니다.

API 키는 환경 변수로 전달할 수도 있습니다.

```bash
export OPENAI_API_KEY="your-api-key"
```

CLI 옵션이 설정 파일과 환경 변수보다 우선합니다. 네트워크에 공개할 경우 이 앱에는 로그인 기능이 없으므로 신뢰할 수 있는 네트워크와 방화벽으로 접근을 제한하세요.

### GPU 관련

lchat은 모델 서버에 HTTP 요청을 보내는 클라이언트입니다. GPU 선택·VRAM 할당·CPU/GPU 오프로딩은 `http://localhost:8070`에서 실행 중인 **모델 서버**가 결정하며 lchat이 원격으로 강제할 수 없습니다. 시작 시의 모델 확인은 `/models` 조회라 추론을 실행하지 않습니다. 실제 GPU 사용 여부는 대화를 보내는 동안 모델 서버에서 `nvidia-smi` 등으로 확인하세요. 생성 요청은 설정한 모델 ID로 `/chat/completions`에 전달됩니다.

## 터미널 UI

```bash
python3 lchat.py --endpoint http://localhost:8070/v1 --model Qwen3.8FN
```

대화형 터미널에서는 ncurses 기반 전체 화면 UI를 사용합니다. 사용자 메시지는 오른쪽 정렬된 선문자 말풍선으로, 말풍선 안의 문장은 왼쪽 정렬로 표시합니다. 긴 문장은 터미널 너비에 맞춰 줄바꿈되고, 입력창은 아래에 고정됩니다. 우측 상단 **설정** 버튼 또는 F2에서 Endpoint, 모델, API 키, 시스템 프롬프트, 생성 옵션, Enter 키 동작, TUI 실행 여부, 웹 UI host/port를 설정할 수 있습니다. 저장 전에 endpoint와 모델을 검사합니다. UI 실행 설정과 웹 호스팅 설정은 다음 실행부터 적용됩니다. 기본은 Enter 전송/Ctrl+J 줄바꿈이며, 설정을 끄면 Enter 줄바꿈/Ctrl+Enter 전송으로 바뀝니다 (일부 터미널에서 Ctrl+Enter가 구별되지 않으면 Ctrl+S로 전송할 수 있습니다). `↑/↓`는 입력 커서의 위/아래 줄을 이동하고 맨 끝에서 입력 기록을 불러옵니다. `PageUp/PageDown` 또는 마우스 휠은 대화를 스크롤하며, 첫 메시지보다 위로 빈 공간까지 넘어가지 않습니다. 응답 중에는 서버 연결 대기, 숨겨진 추론, 텍스트 출력 상태와 움직이는 점을 표시합니다. `<thinking>`/reasoning 내용은 기본으로 접어 두며, 해당 줄을 클릭하거나 F3을 눌러 펼칠 수 있습니다. 응답이 끝나면 Enter 입력부터 첫 텍스트까지의 시간, 첫 텍스트 이후 생성 시간, tok/s, 전체 시간을 표시합니다. 서버가 completion token usage를 제공하면 정확한 속도를, 그렇지 않으면 텍스트 길이 기반 추정치를 표시합니다. `/help`, `/reset`, `/exit` 명령도 지원합니다. 간단한 줄 단위 UI가 필요하면 `--plain`을, 전체 화면 UI를 강제로 켜려면 `--tui`를 지정하세요. Python curses가 포함된 터미널이 필요합니다.

TUI 설정에서 **System prompt**를 선택하고 Enter를 누르면 여러 줄 편집 창이 열립니다. Enter로 줄을 바꾸고 Ctrl+S 또는 F2로 적용하거나 Esc로 취소할 수 있습니다. 편집 내용을 파일에 반영하려면 돌아간 설정 창에서도 **저장**해야 합니다.

터미널이 포커스 이벤트를 지원하면 다른 화면에서 돌아올 때 TUI를 다시 그립니다. 화면이 깨진 경우 **Ctrl+L**로 수동 새로고침할 수 있습니다. 모델 선택과 설정 팝업에서도 동작합니다.

TUI 시작 시 `--model`을 생략하거나 지정한 모델이 서버 목록에 없으면 모델 선택 팝업이 나타납니다. 설정 파일에 저장된 모델이 목록에 있으면 미리 선택되므로 Enter로 바로 확인할 수 있습니다. F4로 모델 목록을 다시 열 수 있습니다. 이 팝업에서 선택한 모델은 현재 실행에 적용되며, 다음 실행에도 사용하려면 F2 설정에서 저장하세요. F2 설정 저장 시에도 모델 ID가 없거나 잘못되면 목록에서 선택할 수 있습니다.

`--reset`은 설정 파일을 기본값(로컬 endpoint, 모델 미선택, TUI 켜짐, 웹 호스팅 꺼짐)으로 초기화한 뒤 실행합니다. 설정을 다시 하려면 TUI의 설정 버튼을 사용하세요.

```bash
python3 lchat.py --reset
# 또는 웹 진입점에서
python3 webchat.py --reset
```

## 요구사항

- Python 3.9 이상
- `GET /models` 및 `POST /chat/completions`를 지원하는 OpenAI 호환 API
- 별도 패키지 설치 불필요
