@echo off
rem YSOYA_Archive (Flask) - Windows quick start
cd /d %~dp0
if not exist .venv (
  echo [1/3] Creating virtual environment...
  py -m venv .venv
)
call .venv\Scripts\activate
echo [2/3] Installing packages...
pip install -q -r requirements.txt
if "%ARCHIVE_ROOTS%"=="" set ARCHIVE_ROOTS=..\ysoya_archive
if not exist data\archive.db (
  echo [3/3] Building index from %ARCHIVE_ROOTS% ...
  python build_index.py --roots %ARCHIVE_ROOTS%
)
echo Open http://127.0.0.1:8000  (Ctrl+C to stop)
waitress-serve --listen=127.0.0.1:8000 app:app
