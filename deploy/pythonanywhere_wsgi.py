# PythonAnywhere 의 WSGI 설정 파일에 붙여 넣을 내용
# (Web 탭 → Code 의 "WSGI configuration file" 링크를 눌러 열리는 파일의 내용을 전부 지우고 아래로 바꿈)
#
# USERNAME 을 내 PythonAnywhere 사용자 이름으로 바꾸세요. 폴더 이름을 다르게 했다면 그것도 바꾸세요.
import os
import sys

PROJECT = "/home/USERNAME/ysoya-archive-flask"
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)
os.chdir(PROJECT)

# 비밀번호 같은 설정은 이 파일이 아니라 프로젝트 폴더의 .env 에 둡니다 (app.py 가 읽음)
from app import app as application  # noqa: E402
