#!/bin/sh
# Install lchat for the current user. No root access or package changes.
set -eu

if [ -z "${HOME:-}" ]; then
    echo '오류: HOME 환경 변수가 설정되지 않았습니다.' >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo '오류: Python 3.9 이상을 먼저 설치하세요.' >&2
    exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
    echo '오류: Python 3.9 이상이 필요합니다.' >&2
    exit 1
fi
if ! python3 -c 'import curses, urllib.request, http.server' >/dev/null 2>&1; then
    echo '오류: Python 표준 라이브러리(curses, urllib, http.server)를 사용할 수 없습니다.' >&2
    echo 'OS의 Python 및 curses 지원 패키지를 설치한 뒤 다시 시도하세요.' >&2
    exit 1
fi
if ! command -v git >/dev/null 2>&1; then
    echo '오류: 프로그램을 내려받으려면 Git을 먼저 설치하세요.' >&2
    exit 1
fi

repository=${LCHAT_REPOSITORY:-https://github.com/ironiris/lchat.git}
install_dir=${LCHAT_INSTALL_DIR:-"$HOME/.local/share/lchat"}
bin_dir=${LCHAT_BIN_DIR:-"$HOME/.local/bin"}
mkdir -p "$(dirname "$install_dir")" "$bin_dir"
install_parent=$(CDPATH= cd -P "$(dirname "$install_dir")" && pwd)
install_dir="$install_parent/$(basename "$install_dir")"
bin_dir=$(CDPATH= cd -P "$bin_dir" && pwd)
target="$install_dir/lchat.py"
link="$bin_dir/lchat"

if [ -e "$link" ] || [ -L "$link" ]; then
    if [ ! -L "$link" ] || [ "$(readlink "$link")" != "$target" ]; then
        printf '오류: %s가 이미 존재하여 덮어쓰지 않았습니다.\n' "$link" >&2
        exit 1
    fi
fi

if [ -e "$install_dir" ] || [ -L "$install_dir" ]; then
    if [ ! -d "$install_dir/.git" ] || [ ! -f "$target" ] || [ ! -f "$install_dir/lchat_web.py" ] || [ ! -f "$install_dir/index.html" ]; then
        printf '오류: %s가 이미 존재하지만 lchat Git 설치가 아닙니다. 덮어쓰지 않았습니다.\n' "$install_dir" >&2
        exit 1
    fi
    printf '기존 설치를 유지합니다: %s\n' "$install_dir"
    echo '최신 버전으로 업데이트하려면 설치 디렉터리에서 git pull --ff-only를 실행하세요.'
else
    temporary=$(mktemp -d "$install_parent/.lchat-install.XXXXXX")
    trap 'rm -rf "$temporary"' EXIT
    trap 'exit 1' HUP INT TERM
    printf '프로그램 다운로드 중: %s\n' "$repository"
    git clone -- "$repository" "$temporary/lchat"
    for file in lchat.py lchat_web.py index.html; do
        if [ ! -f "$temporary/lchat/$file" ]; then
            printf '오류: 다운로드에 필요한 파일이 없습니다: %s\n' "$file" >&2
            exit 1
        fi
    done
    mv "$temporary/lchat" "$install_dir"
    rm -rf "$temporary"
    trap - EXIT HUP INT TERM
fi

if [ ! -x "$target" ]; then
    chmod u+x "$target"
fi
if [ ! -L "$link" ]; then
    ln -s "$target" "$link"
    printf '명령 설치: %s → %s\n' "$link" "$target"
else
    printf '명령이 이미 설치되어 있습니다: %s\n' "$link"
fi
printf '설치 완료: %s\n' "$install_dir"
case ":${PATH:-}:" in
    *":$bin_dir:"*) printf '실행: lchat\n' ;;
    *)
        printf '현재 PATH에 %s가 없습니다. 다음 명령으로 추가하거나 셸 설정 파일에 넣으세요:\n' "$bin_dir"
        printf '  export PATH="%s:$PATH"\n' "$bin_dir"
        printf '지금 바로 실행: "%s"\n' "$link"
        ;;
esac
