#!/bin/bash
# PythonAnywhere 의 Bash 콘솔에서 실행: GitHub 의 최신 코드를 받아 사이트에 반영
#   bash ~/ysoya-archive-flask/deploy/pa_update.sh
# 색인까지 다시 만들려면:  bash ~/ysoya-archive-flask/deploy/pa_update.sh --reindex
set -e
cd "$(dirname "$0")/.."
echo "[1/3] GitHub 에서 최신 코드 받기"
git pull --ff-only
echo "[2/3] 패키지 확인"
"$HOME/.virtualenvs/ysoya/bin/pip" install -q -r requirements.txt
if [ "$1" = "--reindex" ]; then
  echo "     색인 다시 만들기"
  "$HOME/.virtualenvs/ysoya/bin/python" build_index.py --roots "$HOME/ysoya_archive"
fi
echo "[3/3] 웹앱 다시 켜기"
WSGI="/var/www/$(echo "${USER}" | tr 'A-Z.' 'a-z_')_pythonanywhere_com_wsgi.py"
if [ -f "$WSGI" ]; then touch "$WSGI"; echo "완료: https://${USER}.pythonanywhere.com"; else echo "WSGI 파일을 찾지 못했습니다. Web 탭에서 Reload 버튼을 누르세요."; fi
