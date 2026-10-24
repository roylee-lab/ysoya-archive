#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
작은 가짜 자료로 사이트 전체가 동작하는지 확인하는 시험 (GitHub Actions 에서 자동으로 실행)
  python tests/smoke_test.py
"""
import os
import re
import subprocess
import sys
import tempfile

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GIF = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;" + b"\x00" * 7000
PAGE = ('<html><head><meta charset="euc-kr"><title>테스트 대문</title></head><body background="img/back.gif">'
        '<a href="news.shtml">새소식</a> 코믹월드 사진</body></html>').encode("cp949")


def main():
    tmp = tempfile.mkdtemp()
    arc = os.path.join(tmp, "ysoya_archive")
    os.makedirs(os.path.join(arc, "ysoya.net", "img"))
    with open(os.path.join(arc, "ysoya.net", "index.html"), "wb") as f:
        f.write(PAGE)
    with open(os.path.join(arc, "ysoya.net", "img", "back.gif"), "wb") as f:
        f.write(GIF)
    with open(os.path.join(arc, "done.tsv"), "w", encoding="utf-8") as f:
        f.write("original\ttimestamp\tpath\tsize\tsha1\tverified\tnote\n")
        f.write(f"http://ysoya.net:80/\t20030329181549\tysoya.net\\index.html\t{len(PAGE)}\t\tyes\t\n")
        f.write(f"http://ysoya.net:80/img/back.gif\t20030329181549\tysoya.net\\img\\back.gif\t{len(GIF)}\t\tyes\t\n")
    data = os.path.join(tmp, "data")
    env = dict(os.environ, DATA_DIR=data, ARCHIVE_ROOTS=arc, SECRET_KEY="test", SQLITE_JOURNAL="DELETE")
    subprocess.run([sys.executable, "build_index.py", "--roots", arc, "--data", data], cwd=PROJECT, env=env, check=True)
    os.environ.update(env)
    sys.path.insert(0, PROJECT)
    os.chdir(PROJECT)
    import app as A                                     # noqa: E402

    c = A.app.test_client()
    fails = []

    def check(name, cond):
        print(("ok   " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    check("첫 화면", c.get("/").status_code == 200)
    r = c.get("/raw/2003/ysoya.net/")
    body = r.get_data(as_text=True)
    check("보존된 페이지 (EUC-KR → UTF-8)", r.status_code == 200 and "테스트 대문" in body)
    check("링크 다시 쓰기", "/view/20030329181549/ysoya.net/news.shtml" in body)
    check("스크립트 차단 헤더", "script-src 'none'" in r.headers.get("Content-Security-Policy", ""))
    check("그림", c.get("/raw/2003/ysoya.net/img/back.gif").mimetype == "image/gif")
    check("보기 화면", c.get("/view/2003/ysoya.net/").status_code == 200)
    check("찾기", "코믹월드" in c.get("/search?q=코믹월드").get_data(as_text=True))
    for path in ("/browse", "/gallery", "/media", "/timeline", "/about", "/board", "/files", "/person"):
        check(path, c.get(path).status_code == 200)
    check("CSRF 없는 쓰기 거부", c.post("/account/login", data={"name": "x", "password": "y"}).status_code == 400)
    res = A.app.test_cli_runner().invoke(args=["create-admin", "tester", "--password", "testpass123"])
    check("관리자 만들기", res.exit_code == 0)
    tok = lambda h: re.search(r'name="csrf_token" value="([^"]+)"', h).group(1)
    h = c.get("/account/login").get_data(as_text=True)
    c.post("/account/login", data={"name": "tester", "password": "testpass123", "csrf_token": tok(h)})
    h = c.get("/board/free/write").get_data(as_text=True)
    r = c.post("/board/free/write", data={"title": "시험 글", "body": "<b>굵게</b>", "csrf_token": tok(h)})
    check("글쓰기", r.status_code == 302)
    page = c.get(r.headers.get("Location", "/")).get_data(as_text=True)
    check("글의 HTML 무력화", "&lt;b&gt;" in page and "<b>굵게</b>" not in page)
    print(f"\n{'모두 통과' if not fails else f'실패 {len(fails)}개'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
