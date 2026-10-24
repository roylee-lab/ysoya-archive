#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YSOYA_Archive 색인 만들기

ysoya_recover.py 로 받은 폴더(들)를 읽어 data/archive.db 를 만든다.
웹 서버는 이 파일만 보고 페이지 목록·검색·사진첩·연대기를 보여준다.

사용 예
  python build_index.py --roots ../ysoya_archive
  python build_index.py --roots ../ysoya_archive --roots ../ysoya_archive_phantagram --roots ../found_files
  python build_index.py --roots ../ysoya_archive --convert-media     (ffmpeg 필요: 옛 영상·음성을 브라우저용으로 변환)

설정(site_config.json)을 바꾼 뒤에는 다시 실행해야 반영된다. 몇 분 걸린다.
"""
import argparse
import base64
import collections
import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time

import archive_core as ac

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

SCHEMA = """
CREATE TABLE captures (
  id INTEGER PRIMARY KEY, root TEXT, file TEXT,
  url TEXT, canon TEXT, canon_lc TEXT, host TEXT, path TEXT, dir TEXT, name TEXT, ext TEXT,
  ts TEXT, year INTEGER, mime TEXT, kind TEXT, sniff TEXT,
  size INTEGER, sha1 TEXT, verified INTEGER,
  junk INTEGER DEFAULT 0, junk_reason TEXT, hidden INTEGER DEFAULT 0, section TEXT,
  title TEXT, derived TEXT
);
CREATE INDEX ix_canon ON captures(canon);
CREATE INDEX ix_canon_lc ON captures(canon_lc);
CREATE INDEX ix_dir ON captures(dir);
CREATE INDEX ix_kind ON captures(kind, hidden, junk);
CREATE INDEX ix_year ON captures(year);
CREATE TABLE page_text (id INTEGER PRIMARY KEY, body TEXT);
CREATE TABLE albums (id INTEGER PRIMARY KEY, dir TEXT, title TEXT, date TEXT, count INTEGER, cover INTEGER, section TEXT);
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
"""
MAX_PAGE_BYTES = 3 * 1024 * 1024
MAX_TEXT = 30000


def log(msg):
    print(msg, flush=True)


def b32_sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return base64.b32encode(h.digest()).decode()


def read_done(root):
    p = os.path.join(root, "done.tsv")
    if not os.path.isfile(p):
        log(f"  ! {p} 가 없어 건너뜀 (ysoya_recover.py 로 받은 폴더인지 확인)")
        return []
    with open(p, encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f, delimiter="\t") if r.get("original") and r.get("path")]


def album_title(d, cfg):
    over = cfg.get("album_titles", {})
    if d in over:
        v = over[d]
        return (v[0], v[1]) if isinstance(v, list) else (v, "")
    parts = [x for x in d.split("/") if x]
    last = parts[-1] if parts else d
    parent = parts[-2] if len(parts) > 1 else ""
    m = re.match(r"^(\d{4})-(\d{2})(\d{2})(.*)$", last)
    if m:
        return f"{m.group(1)}.{m.group(2)}.{m.group(3)} {m.group(4).strip('_- ')}".strip(), f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"^(\d{4})(\d{2})$", last)
    if m and "diary" in d:
        return f"일기 사진 {m.group(1)}.{m.group(2)}", f"{m.group(1)}-{m.group(2)}-01"
    m = re.match(r"^(\d{2})(\d{2})(.+)$", last)
    pm = re.match(r"^(\d{4})", parent)
    if m and pm:
        return f"{pm.group(1)}.{m.group(1)}.{m.group(2)} {m.group(3)}", f"{pm.group(1)}-{m.group(1)}-{m.group(2)}"
    return last, ""


def convert_media(db, data_dir):
    ff = shutil.which("ffmpeg")
    if not ff:
        log("  ! ffmpeg 를 찾지 못해 변환을 건너뜀 (설치 후 PATH 에 추가)")
        return
    out_dir = os.path.join(data_dir, "derived")
    os.makedirs(out_dir, exist_ok=True)
    rows = db.execute("SELECT id, root, file, ext, kind, sha1 FROM captures WHERE kind IN ('video','audio') "
                      "AND hidden=0 AND junk=0").fetchall()
    done_by_hash = {}
    for r in rows:
        if r["sha1"] in done_by_hash:                   # 같은 내용은 한 번만 변환
            db.execute("UPDATE captures SET derived=? WHERE id=?", (done_by_hash[r["sha1"]], r["id"]))
            continue
        if r["ext"] in ac.BROWSER_PLAYABLE or r["ext"] in (".mid", ".midi", ".swf", ".s3m", ".xm", ".mod", ".it"):
            continue
        src = ROOTS[r["root"]] and os.path.join(ROOTS[r["root"]], r["file"])
        target = f"{r['id']}.mp4" if r["kind"] == "video" else f"{r['id']}.mp3"
        dst = os.path.join(out_dir, target)
        if not os.path.exists(dst):
            cmd = [ff, "-v", "error", "-y", "-i", src]
            cmd += (["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"] if r["kind"] == "video"
                    else ["-vn", "-c:a", "libmp3lame", "-b:a", "160k"])
            log(f"  변환: {r['file']} → derived/{target}")
            res = subprocess.run(cmd + [dst], capture_output=True, text=True)
            if res.returncode != 0 or not os.path.exists(dst):
                log(f"    실패: {res.stderr.strip()[:200]}")
                continue
        db.execute("UPDATE captures SET derived=? WHERE id=?", ("derived/" + target, r["id"]))
        done_by_hash[r["sha1"]] = "derived/" + target
    db.commit()


ROOTS = {}


def main():
    ap = argparse.ArgumentParser(description="YSOYA_Archive 색인 만들기")
    ap.add_argument("--roots", action="append", default=[], help="받은 폴더 (여러 번 지정 가능)")
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--config", default=os.environ.get("SITE_CONFIG", "site_config.json"))
    ap.add_argument("--convert-media", action="store_true")
    a = ap.parse_args()
    roots = a.roots or [p for p in re.split(r"[;,]", os.environ.get("ARCHIVE_ROOTS", "")) if p]
    if not roots:
        sys.exit("--roots 로 받은 폴더를 지정하세요. 예: --roots ../ysoya_archive")
    cfg = ac.load_config(a.config)
    for r in roots:
        if not os.path.isdir(r):
            sys.exit(f"폴더가 없습니다: {r}")
        key = os.path.basename(os.path.normpath(r))
        if key in ROOTS:
            sys.exit(f"폴더 이름이 겹칩니다: {key} (폴더 이름은 서로 달라야 합니다)")
        ROOTS[key] = os.path.abspath(r)

    os.makedirs(a.data, exist_ok=True)
    db_path = os.path.join(a.data, "archive.db")
    tmp_path = db_path + ".building"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    db = sqlite3.connect(tmp_path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    fts = True
    try:
        db.execute("CREATE VIRTUAL TABLE page_fts USING fts5(title, body, content='', tokenize='trigram')")
    except sqlite3.OperationalError:
        fts = False
        log("  (이 파이썬의 SQLite 는 trigram 검색을 지원하지 않아 단순 검색만 사용)")

    t0 = time.time()
    seen = set()
    n = 0
    for key, root in ROOTS.items():
        rows = read_done(root)
        log(f"[{key}] 기록 {len(rows)}개 읽는 중...")
        for i, r in enumerate(rows, 1):
            rel = r["path"].replace("\\", "/").lstrip("/")
            full = os.path.join(root, rel)
            if not os.path.isfile(full):
                continue
            c = ac.canon(r["original"])
            ts = re.sub(r"\D", "", r.get("timestamp", ""))[:14]
            sha1 = (r.get("sha1") or "").strip()
            if len(sha1) != 32:
                sha1 = b32_sha1(full)
            dedup = (c, ts, sha1)
            if dedup in seen:
                continue
            seen.add(dedup)
            host, path, _ = ac.split_canon(c)
            d = host + path.rsplit("/", 1)[0] + "/"
            name = path.rsplit("/", 1)[-1]
            ext = ac.ext_of(c)
            size = os.path.getsize(full)
            with open(full, "rb") as f:
                head = f.read(MAX_PAGE_BYTES if ext in ac.PAGE_EXT else 65536)
            sn = ac.sniff(head)
            kind = ac.kind_of(ext, sn)
            junk, why = 0, None
            if ts and ts[:len(cfg.get("junk_after", "9999"))] >= cfg.get("junk_after", "9999"):
                junk, why = 1, "late"
            elif ext not in ac.PAGE_EXT and sn == "html":
                junk, why = 1, "html-instead"
            elif ext in ac.EXPECT and sn != ac.EXPECT[ext]:
                junk, why = 1, "mismatch"
            hidden = 1 if ac.is_hidden(c, cfg) else 0
            title, body = "", ""
            if kind == "page" and not junk:
                text, _ = ac.decode_page(head)
                title = ac.page_title(text)
                if not hidden:
                    body = ac.strip_tags(text)[:MAX_TEXT]
            if not title:
                title = name or host
            cur = db.execute(
                "INSERT INTO captures (root,file,url,canon,canon_lc,host,path,dir,name,ext,ts,year,mime,kind,sniff,"
                "size,sha1,verified,junk,junk_reason,hidden,section,title) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, rel, r["original"], c, c.lower(), host, path, d, name, ext, ts, int(ts[:4] or 0),
                 ac.MIME.get(ext, "application/octet-stream"), kind, sn, size, sha1,
                 1 if r.get("verified") == "yes" else 0, junk, why, hidden, ac.section_of(c, cfg), title))
            if body:
                db.execute("INSERT INTO page_text (id, body) VALUES (?,?)", (cur.lastrowid, body))
            n += 1
            if i % 2000 == 0:
                log(f"  {i}/{len(rows)}")
    db.commit()
    log(f"캡처 {n}개 등록 ({time.time() - t0:.0f}초)")

    # 여러 주소에서 똑같은 내용이 나오는 페이지 = 서버의 '없는 페이지' 응답 → 쓸모없는 캡처로 표시
    groups = collections.defaultdict(set)
    for r in db.execute("SELECT sha1, path FROM captures WHERE junk=0 AND (kind='page' OR sniff='html')"):
        groups[r["sha1"]].add(r["path"])
    bad = [h for h, paths in groups.items() if len(paths) >= int(cfg.get("junk_cluster_min", 8))]
    for h in bad:
        db.execute("UPDATE captures SET junk=1, junk_reason='cluster' WHERE sha1=?", (h,))
    db.execute("DELETE FROM page_text WHERE id IN (SELECT id FROM captures WHERE junk=1)")
    db.commit()
    log(f"쓸모없는 캡처 표시: {db.execute('SELECT COUNT(*) FROM captures WHERE junk=1').fetchone()[0]}개")

    if fts:
        log("검색 색인 만드는 중...")
        db.execute("INSERT INTO page_fts (rowid, title, body) SELECT c.id, c.title, t.body "
                   "FROM captures c JOIN page_text t ON t.id=c.id")
        db.commit()

    # 사진첩: 사진이 4장 이상 있는 폴더
    log("사진첩 정리 중...")
    by_dir = collections.defaultdict(dict)
    for r in db.execute("SELECT id, canon, dir, name, ts, size, section FROM captures WHERE kind='image' "
                        "AND hidden=0 AND junk=0 AND size>6000 AND canon NOT LIKE '%/.thumbnails/%' "
                        "AND canon NOT LIKE '%/icon/%' ORDER BY ts"):
        by_dir[r["dir"]][r["canon"]] = r
    for d, imgs in by_dir.items():
        if len(imgs) < 4:
            continue
        title, date = album_title(d.rstrip("/"), cfg)
        first = sorted(imgs.values(), key=lambda r: r["name"].lower())[0]
        db.execute("INSERT INTO albums (dir,title,date,count,cover,section) VALUES (?,?,?,?,?,?)",
                   (d, title, date, len(imgs), first["id"], first["section"]))
    db.commit()

    # 일기장 달력
    diary = {}
    for r in db.execute("SELECT canon, path, ts FROM captures WHERE kind='page' AND hidden=0 AND junk=0"):
        m = cfg["_diary"].search(r["path"])
        if not m:
            continue
        yy, mm = int(m.group(1)), int(m.group(2))
        year = 1900 + yy if yy >= 90 else 2000 + yy
        key = f"{year}-{mm:02d}"
        if key not in diary or r["ts"] > diary[key]["ts"]:
            diary[key] = {"ym": key, "canon": r["canon"], "ts": r["ts"]}

    # 통계
    vis = "hidden=0 AND junk=0"
    stats = {
        "pages": db.execute(f"SELECT COUNT(DISTINCT canon) FROM captures WHERE kind='page' AND {vis}").fetchone()[0],
        "images": db.execute(f"SELECT COUNT(DISTINCT canon) FROM captures WHERE kind='image' AND {vis}").fetchone()[0],
        "media": db.execute(f"SELECT COUNT(DISTINCT canon) FROM captures WHERE kind IN ('video','audio') AND {vis}").fetchone()[0],
        "captures": db.execute(f"SELECT COUNT(*) FROM captures WHERE {vis}").fetchone()[0],
        "albums": db.execute("SELECT COUNT(*) FROM albums").fetchone()[0],
        "hidden": db.execute("SELECT COUNT(*) FROM captures WHERE hidden=1 AND junk=0").fetchone()[0],
        "junk": db.execute("SELECT COUNT(*) FROM captures WHERE junk=1").fetchone()[0],
        "first": db.execute(f"SELECT MIN(ts) FROM captures WHERE {vis}").fetchone()[0],
        "last": db.execute(f"SELECT MAX(ts) FROM captures WHERE {vis}").fetchone()[0],
        "years": {str(y): c for y, c in db.execute(f"SELECT year, COUNT(*) FROM captures WHERE {vis} GROUP BY year")},
    }
    meta = {"stats": stats, "diary": sorted(diary.values(), key=lambda x: x["ym"]), "fts": fts,
            "roots": list(ROOTS), "built": time.strftime("%Y-%m-%d %H:%M")}
    for k, v in meta.items():
        db.execute("INSERT INTO meta (k, v) VALUES (?,?)", (k, json.dumps(v, ensure_ascii=False)))
    db.commit()

    if a.convert_media:
        log("영상·음성 변환 중...")
        convert_media(db, a.data)
    db.close()
    try:
        os.replace(tmp_path, db_path)
    except PermissionError:
        sys.exit(f"\n새 색인은 {tmp_path} 에 만들었지만 기존 파일을 바꾸지 못했습니다.\n"
                 "웹 서버가 켜져 있으면 끈 뒤 이 명령을 다시 실행하세요. (윈도우는 사용 중인 파일을 바꿀 수 없습니다)")
    log(f"\n완료: {db_path}")
    log(f"  페이지 {stats['pages']} / 이미지 {stats['images']} / 영상·음성 {stats['media']} / 사진첩 {stats['albums']}")
    log(f"  숨김 {stats['hidden']} / 쓸모없는 캡처 {stats['junk']} / 일기장 {len(diary)}개월")


if __name__ == "__main__":
    main()
