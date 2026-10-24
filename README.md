# YSOYA_Archive — Flask 판

`ysoya_recover.py`로 받은 폴더를 그대로 웹사이트로 보여줍니다.

- 보존된 페이지를 캡처 당시 모습 그대로 열고, 같은 주소의 다른 시점으로 오갑니다.
- 주소 둘러보기, 사진첩, 영상·음성, 일기장 달력, 연대기, 본문 찾기를 제공합니다.
- 옛 한국어(EUC-KR)·일본어 페이지를 글자 깨짐 없이 보여줍니다.
- 보존된 페이지 안의 스크립트는 실행되지 않고, 바깥 사이트로 몰래 접속하지도 않습니다.

## 폴더 배치

```
작업 폴더/
├─ ysoya_archive/            ← ysoya_recover.py 로 받은 폴더 (done.tsv 가 들어 있어야 함)
├─ ysoya_archive_phantagram/ ← (있으면) 다른 위치에서 받은 폴더
├─ found_files/              ← (있으면) find_links.py 로 찾은 파일
└─ ysoya-archive-flask/      ← 이 프로젝트
```

## 빠른 시작 (윈도우)

`run_windows.bat`을 더블클릭하면 아래 과정을 한 번에 합니다.

1. 가상환경 만들기
2. 패키지 설치
3. 색인 만들기 (처음 한 번)
4. 서버 켜기

다 되면 브라우저에서 http://127.0.0.1:8000 을 엽니다.

직접 하려면 PowerShell에서 아래처럼 입력합니다.

```powershell
cd ysoya-archive-flask
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

python build_index.py --roots ..\ysoya_archive
$env:ARCHIVE_ROOTS = "..\ysoya_archive"
python app.py
```

받은 폴더가 여러 개면 둘 다 여러 번 또는 쉼표로 지정합니다.

```powershell
python build_index.py --roots ..\ysoya_archive --roots ..\ysoya_archive_phantagram --roots ..\found_files
$env:ARCHIVE_ROOTS = "..\ysoya_archive,..\ysoya_archive_phantagram,..\found_files"
```

> 색인(`data/archive.db`)에는 파일 위치가 '폴더 이름 + 그 안의 경로'로만 저장됩니다.
> 그래서 폴더를 다른 컴퓨터로 옮겨도 `ARCHIVE_ROOTS`만 맞춰 주면 됩니다.
> 단, 폴더 이름(`ysoya_archive` 등)은 바꾸지 마세요.

## 설정 (`site_config.json`)

설정을 바꾼 뒤에는 `build_index.py`를 다시 실행해야 반영됩니다.

| 항목 | 뜻 |
|---|---|
| `hidden_prefixes` | 보이지 않게 할 주소 앞부분. 기본으로 방명록, 다른 사람의 홈페이지, 상업 영상·음성 폴더가 들어 있음 |
| `hidden_exts` | 보이지 않게 할 확장자 (실행 파일) |
| `junk_after` | 이 해부터의 캡처는 무관한 페이지로 보고 뺌 (기본 2017) |
| `featured` | 첫 화면 '다시 열어볼 곳' 목록 |
| `timeline` | 연대기 |
| `album_titles` | 사진첩 이름과 날짜 |
| `hero` | 첫 화면 창에 띄울 페이지와 시점 |
| `mirrors` | 같은 홈페이지가 있던 여러 위치. 한 곳에 없는 파일을 다른 곳에서 찾음 |
| `allow_indexing` | `true`면 검색엔진 수집 허용 (기본 `false`) |
| `allow_scripts` | `true`면 보존된 페이지의 옛 스크립트 실행 (기본 `false`, 권장) |
| `contact` | 삭제 요청을 받을 이메일 (공개 운영이라면 꼭 적기) |

## 비공개로 운영하기

환경 변수 `SITE_PASSWORD`를 정하면 비밀번호를 아는 사람만 들어올 수 있습니다.

```powershell
$env:SITE_PASSWORD = "비밀번호"
$env:SECRET_KEY = "아무렇게나-길게-쓴-영문과-숫자"
```

`SECRET_KEY`는 로그인을 유지하는 데 쓰입니다. 정하지 않으면 서버를 껐다 켤 때마다 다시 로그인해야 합니다. HTTPS 주소로 운영할 때는 `COOKIE_SECURE=1`도 정하세요.

## PythonAnywhere + GitHub 로 배포하기 (요약)

코드는 GitHub에 두고, PythonAnywhere가 그 코드를 받아 실행합니다. 받은 자료는 GitHub에 올리지 않고 PythonAnywhere에 직접 올립니다. 자세한 순서는 함께 드린 'PythonAnywhere + GitHub 배포 가이드'를 보세요.

1. **내 컴퓨터**: 색인을 만든 뒤 보이는 파일만 묶습니다. 숨김 구역은 빠져서 약 50MB짜리 zip 하나가 됩니다.

    ```powershell
    python build_index.py --roots ..\ysoya_archive
    python tools\pack_for_upload.py --roots ..\ysoya_archive
    ```

2. **GitHub**: 이 폴더를 저장소로 올립니다. `.gitignore`가 자료, 색인, `.env`를 빼 줍니다.
3. **PythonAnywhere 콘솔**: 코드를 받고(`git clone`), 가상환경을 만들고, `upload/` 안의 zip을 Files 탭으로 올려 홈 폴더에 풉니다.
4. **설정**: `deploy/env.pythonanywhere.example`을 `.env`로, `deploy/site_config.local.example.json`을 `site_config.local.json`으로 복사해 고칩니다.
5. **색인·관리자·웹앱**: 색인을 만들고 관리자를 만든 뒤, Web 탭에서 수동 설정(Manual configuration)으로 웹앱을 만듭니다. WSGI 파일 내용은 `deploy/pythonanywhere_wsgi.py`에 있습니다.
6. **고친 뒤 반영**: 내 컴퓨터에서 `git push`, PythonAnywhere 콘솔에서 `bash ~/ysoya-archive-flask/deploy/pa_update.sh`.

**서버별 설정은 `site_config.local.json`에 둡니다.** 이 파일이 있으면 `site_config.json` 위에 덮어씌워 읽습니다. 서버에서 `site_config.json`을 직접 고치면 다음 `git pull` 때 부딪히므로, 서버마다 다른 값은 이 파일에 두세요. GitHub에는 올라가지 않습니다.

**환경 변수는 `.env`에 적어도 됩니다.** `app.py`가 프로젝트 폴더의 `.env`를 읽습니다. 이미 정해진 환경 변수가 있으면 그쪽이 우선합니다.

**자동 시험.** GitHub에 올릴 때마다 Actions가 `tests/smoke_test.py`를 돌려 사이트가 동작하는지 확인합니다. 내 컴퓨터에서도 `python tests/smoke_test.py`로 돌릴 수 있습니다.

## 커뮤니티 (게시판, 자료, 인물)

| 주소 | 내용 |
|---|---|
| `/board` | 게시판 목록. 공지(관리자만 씀), 자유게시판, 발굴·제보, 자료 나눔, 질문 |
| `/files` | 홈페이지에 있던 파일 + 회원이 게시글에 붙인 파일 |
| `/person` | 김용하의 경력. `career.json`을 고치면 바로 반영 (출처 포함) |
| `/account/join`, `/account/login` | 가입, 로그인 |
| `/admin` | 관리자 화면: 신고 처리, 초대 코드, 회원 정지, 숨긴 글 되살리기 |

**처음 한 번: 관리자 만들기.** 서버에서 아래를 실행하면 비밀번호를 묻습니다.

```powershell
flask --app app create-admin 운영자
```

그다음 사이트에서 그 닉네임으로 로그인하고, 관리 화면에서 초대 코드를 만들어 친구들에게 나눠 주면 됩니다. 비밀번호를 잊은 회원은 `flask --app app reset-password 닉네임`으로 새로 정해 줍니다.

**설정 (`site_config.json`의 `community`)**

| 항목 | 뜻 |
|---|---|
| `signup` | `invite`(초대 코드가 있어야 가입, 기본) / `open`(누구나) / `closed`(가입 막음) |
| `members_only` | `true`면 회원만 사이트 전체를 볼 수 있음. 비공개 운영에 `SITE_PASSWORD` 대신 쓰기 좋음 |
| `boards` | 게시판 목록. `admin_only: true`면 관리자만 글을 씀 |
| `max_upload_mb`, `max_files_per_post`, `allowed_exts` | 첨부 파일 크기·개수·형식 제한 |

**지켜지는 것들**

- **최소한의 개인정보**: 회원 정보는 닉네임과 비밀번호 해시뿐입니다. 이메일, 실명, IP는 저장하지 않습니다.
- **글 내용 보호**: 글은 글자 그대로 보이고 HTML은 모두 무력화됩니다. 주소만 링크가 됩니다.
- **요청 위조 방지**: 모든 쓰기 요청에 CSRF 토큰을 확인합니다.
- **도배 방지**: 같은 사람이 글은 20초, 댓글은 8초 간격으로만 쓸 수 있습니다.
- **첨부 파일 검사**: 허용한 형식만 받습니다. 그림은 이름뿐 아니라 내용까지 확인하고, 실행 파일과 HTML은 받지 않습니다. 그림이 아닌 파일은 항상 내려받기로만 열립니다.
- **삭제는 숨김**: 지운 글은 실제로 지우지 않고 숨겨서, 관리자가 되살릴 수 있습니다.

**백업할 것.** 게시판 데이터는 `data/community.db`에, 첨부 파일은 `data/uploads/`에 있습니다. `archive.db`와 달리 **다시 만들 수 없으니** 꼭 백업하세요.

## 옛 영상·음성 재생

MPG, WMV, ASF, RM, VQF 같은 형식은 브라우저가 재생하지 못합니다. [ffmpeg](https://ffmpeg.org/)를 설치한 뒤 색인을 만들 때 `--convert-media`를 붙이면, 재생할 수 있는 MP4·MP3를 `data/derived/`에 만들어 둡니다.

```powershell
python build_index.py --roots ..\ysoya_archive --convert-media
```

## 파일 구성

| 파일 | 하는 일 |
|---|---|
| `app.py` | 웹 서버 (주소별 화면, 로그인, 보안 헤더) |
| `archive_core.py` | 주소 정규화, 글자 인코딩, 링크 다시 쓰기, 캡처 찾기 |
| `build_index.py` | 받은 폴더를 읽어 `data/archive.db` 만들기 |
| `site_config.json` | 숨길 구역, 첫 화면 목록, 연대기, 커뮤니티 설정 |
| `community.py` | 게시판, 회원, 첨부 파일, 관리, 자료·인물 페이지 |
| `career.json` | 인물 페이지의 경력 자료와 출처 |
| `templates/`, `static/` | 화면 틀과 디자인 |
| `tools/pack_for_upload.py` | 보이는 파일만 골라 업로드용 zip 만들기 (100MB 이하로 나눔) |
| `deploy/` | PythonAnywhere용 WSGI 파일, 업데이트 스크립트, `.env`·로컬 설정 예시 |
| `tests/smoke_test.py`, `.github/workflows/check.yml` | 작은 가짜 자료로 사이트 전체를 시험 (GitHub에서 자동 실행) |
| `Dockerfile`, `docker-compose.yml` | 서버에 올릴 때 |

## 주소 규칙

| 주소 | 뜻 |
|---|---|
| `/view/20030329181549/ysoya.net/` | 보기 화면 (위쪽 도구 막대 + 보존된 페이지) |
| `/raw/20030329181549/ysoya.net/` | 보존된 페이지 자체 (링크가 이 사이트 주소로 바뀐 상태) |
| `/view/2001/ysoya.net/works.shtml` | 시점을 연도만 써도 가장 가까운 캡처를 찾음 |

## 문제 해결

| 증상 | 해결 |
|---|---|
| "색인이 아직 없습니다" | `build_index.py`를 먼저 실행 |
| 페이지는 뜨는데 그림이 안 보임 | `ARCHIVE_ROOTS`가 받은 폴더를 가리키는지 확인 |
| 500 오류: "폴더 위치를 모릅니다" | 색인을 만들 때 쓴 폴더 이름과 `ARCHIVE_ROOTS`의 폴더 이름이 같은지 확인 |
| 찾기가 느림 | 두 글자 이하 낱말은 전체를 훑기 때문. 세 글자 이상은 색인을 씀 |

서버에 올리는 방법은 함께 드린 배포 가이드를 보세요.
