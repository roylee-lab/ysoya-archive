# -*- coding: utf-8 -*-
"""
YSOYA_Archive — Flask 판

실행 (개발용):   python app.py
실행 (운영용):   waitress-serve --port=8000 app:app      (윈도우)
                 gunicorn -w 2 -b 0.0.0.0:8000 app:app    (리눅스)

환경 변수
  ARCHIVE_ROOTS  받은 폴더들 (쉼표로 구분)  예) ../ysoya_archive,../found_files
  DATA_DIR       archive.db 가 있는 폴더 (기본: ./data)
  SITE_PASSWORD  정하면 비밀번호를 아는 사람만 볼 수 있음 (비공개 운영)
  SECRET_KEY     로그인 유지용 비밀 값 (비밀번호를 쓸 때 꼭 정하기)
  COOKIE_SECURE  HTTPS 로 운영하면 1
"""
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import time

from flask import (Flask, Response, abort, g, redirect, render_template, request, send_file,
                   session, url_for)
from markupsafe import Markup, escape

import archive_core as ac
import community

BASE = os.path.dirname(os.path.abspath(__file__))


def load_dotenv(path):
    """프로젝트 폴더의 .env 를 읽어 환경 변수로 넣는다. 이미 정해진 환경 변수가 우선."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            os.environ.setdefault(key.strip(), val)


load_dotenv(os.path.join(BASE, ".env"))
DATA_DIR = os.path.abspath(os.environ.get("DATA_DIR", os.path.join(BASE, "data")))
DB_PATH = os.path.join(DATA_DIR, "archive.db")
CFG = ac.load_config(os.environ.get("SITE_CONFIG", os.path.join(BASE, "site_config.json")))
PASSWORD = os.environ.get("SITE_PASSWORD", "")
PER_PAGE = 20
SEARCH_LIMIT = 400
BROWSE_LIMIT = 500

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(MAX_CONTENT_LENGTH=int(CFG.get("community", {}).get("max_upload_mb", 20)) * 1048576 * 6,
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30)

RAW_CSP = ("default-src 'self' data:; img-src 'self' data:; media-src 'self'; style-src 'self' 'unsafe-inline'; "
           "font-src 'self' data:; script-src {scripts}; object-src 'none'; form-action 'none'; base-uri 'none'; "
           "frame-src 'self'; frame-ancestors 'self'").format(
    scripts="'self' 'unsafe-inline'" if CFG.get("allow_scripts") else "'none'")
APP_CSP = ("default-src 'self'; img-src 'self' data:; media-src 'self'; script-src 'self'; "
           "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
           "frame-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'")


# ------------------------------------------------------------------ 데이터베이스
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


_META = {}


def meta():
    """색인을 다시 만들면 자동으로 새로 읽는다 (파일 수정 시각으로 판단)."""
    try:
        mtime = os.path.getmtime(DB_PATH)
    except OSError:
        return None
    if _META.get("mtime") != mtime:
        db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        _META.clear()
        _META.update({k: json.loads(v) for k, v in db.execute("SELECT k, v FROM meta")})
        _META["mtime"] = mtime
        _META["sections"] = [r[0] for r in db.execute(
            "SELECT section FROM captures WHERE hidden=0 AND junk=0 AND kind='page' GROUP BY section "
            "ORDER BY COUNT(*) DESC")]
        db.close()
        _META.pop("featured", None)
    return _META


def root_paths():
    env = [p for p in re.split(r"[;,]", os.environ.get("ARCHIVE_ROOTS", "")) if p.strip()]
    paths = {os.path.basename(os.path.normpath(p)): os.path.abspath(p) for p in env}
    for name in (meta() or {}).get("roots", []):       # 지정하지 않았으면 이 폴더 옆에서 찾기
        paths.setdefault(name, os.path.abspath(os.path.join(BASE, "..", name)))
    return paths


def file_path(cap):
    root = root_paths().get(cap["root"])
    if not root:
        abort(500, description=f"받은 폴더 '{cap['root']}' 의 위치를 모릅니다. ARCHIVE_ROOTS 를 확인하세요.")
    full = os.path.realpath(os.path.join(root, cap["file"]))
    if not full.startswith(os.path.realpath(root) + os.sep) or not os.path.isfile(full):
        abort(404)
    return full


# ------------------------------------------------------------------ 공통
@app.context_processor
def inject():
    return {"cfg": CFG, "meta": meta() or {}, "view_href": ac.view_href, "raw_href": ac.raw_href,
            "ts_label": ac.ts_label}


os.makedirs(DATA_DIR, exist_ok=True)
community.init_app(app, CFG, DATA_DIR, BASE, get_db)
OPEN_ENDPOINTS = {"login", "static", "robots", "healthz"}
MEMBER_OPEN = OPEN_ENDPOINTS | {"community.login", "community.join"}


@app.before_request
def guard():
    if request.method == "POST" and not community.csrf_ok():
        return Response("요청이 만료됐습니다. 페이지를 새로 고친 뒤 다시 시도하세요.", 400)
    if meta() is None and request.endpoint not in ("static", "healthz") and not (request.endpoint or "").startswith("community."):
        return render_template("setup.html", active=None), 503
    if CFG.get("community", {}).get("members_only") and not g.user and request.endpoint not in MEMBER_OPEN:
        if request.path.startswith(("/raw/", "/file/", "/derived/", "/raw-file/", "/u/")):
            return Response("로그인이 필요합니다.", 401)
        return redirect(url_for("community.login", next=request.full_path))
    if not PASSWORD or request.endpoint in OPEN_ENDPOINTS or session.get("ok"):
        return None
    if request.path.startswith(("/raw/", "/file/", "/derived/", "/raw-file/", "/u/")):
        return Response("로그인이 필요합니다.", 401)
    return redirect(url_for("login", next=request.full_path))


@app.after_request
def headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Content-Security-Policy", APP_CSP)
    if not CFG.get("allow_indexing"):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


_FAILS = {}


@app.route("/login", methods=["GET", "POST"])
def login():
    nxt = request.values.get("next", "/")
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    if not PASSWORD:
        return redirect(nxt)
    error = None
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if _FAILS.get(ip, 0) >= 5:
            time.sleep(2)                       # 연속으로 틀리면 조금씩 늦춤
        if hmac.compare_digest(request.form.get("password", "").encode(), PASSWORD.encode()):
            _FAILS.pop(ip, None)
            session.permanent = True
            session["ok"] = True
            return redirect(nxt)
        _FAILS[ip] = _FAILS.get(ip, 0) + 1
        error = "비밀번호가 맞지 않습니다. 다시 입력하세요."
    return render_template("login.html", next=nxt, error=error, active=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/robots.txt")
def robots():
    body = "User-agent: *\nAllow: /\n" if CFG.get("allow_indexing") else "User-agent: *\nDisallow: /\n"
    return Response(body, mimetype="text/plain")


@app.route("/healthz")
def healthz():
    return {"ok": os.path.exists(DB_PATH)}


# ------------------------------------------------------------------ 첫 화면·연대기
def year_rows():
    years = sorted((y, n) for y, n in meta()["stats"]["years"].items() if y != "0")
    return years, max([n for _, n in years] or [1])


def diary_ctx():
    diary = meta().get("diary", [])
    dmap = {d["ym"]: d for d in diary}
    ys = sorted({int(d["ym"][:4]) for d in diary})
    return {"diary_map": dmap, "diary_years": list(range(ys[0], ys[-1] + 1)) if ys else []}


def featured():
    m = meta()
    if "featured" not in m:
        out = []
        db = get_db()
        for f in CFG.get("featured", []):
            cap, _, _ = ac.find_capture(db, ac.canon(f["url"]), f.get("ts", "2003"), CFG)
            if cap:
                out.append({**f, "href": ac.view_href(cap["ts"], cap["canon"])})
        m["featured"] = out
    return m["featured"]


@app.route("/")
def home():
    m = meta()
    db = get_db()
    hero = CFG.get("hero", {"url": "ysoya.net/", "ts": "2003"})
    hero_canon = ac.canon(hero["url"])
    cap, _, _ = ac.find_capture(db, hero_canon, hero["ts"], CFG)
    title_cap, _, _ = ac.find_capture(db, "ysoya.net/img/title.gif", hero["ts"], CFG)
    years, ymax = year_rows()
    albums = db.execute("SELECT * FROM albums ORDER BY count DESC LIMIT 8").fetchall()
    return render_template(
        "home.html", active="home", stats=m["stats"], years=years, years_max=ymax, hero_canon=hero_canon,
        hero_raw=ac.raw_href(cap["ts"], cap["canon"]) if cap else "about:blank",
        hero_view=ac.view_href(cap["ts"], cap["canon"]) if cap else url_for("browse"),
        hero_label=f"{cap['ts'][:4]}년 {int(cap['ts'][4:6])}월 {int(cap['ts'][6:8])}일" if cap else "",
        title_img=ac.raw_href(title_cap["ts"], title_cap["canon"]) if title_cap else None,
        featured=featured(), albums=albums, **diary_ctx())


@app.route("/timeline")
def timeline():
    years, ymax = year_rows()
    hero_canon = ac.canon(CFG.get("hero", {}).get("url", "ysoya.net/"))
    return render_template("timeline.html", active="timeline", years=years, years_max=ymax,
                           hero_canon=hero_canon, **diary_ctx())


@app.route("/about")
def about():
    return render_template("about.html", active="about", stats=meta()["stats"])


# ------------------------------------------------------------------ 둘러보기
def like_prefix(p):
    return p.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@app.route("/browse")
def browse():
    db = get_db()
    p = request.args.get("p", "").strip().lstrip("/")
    variant = request.args.get("f", "")
    if not p:
        hosts = db.execute("SELECT host, COUNT(DISTINCT canon) n, MIN(ts) a, MAX(ts) b FROM captures "
                           "WHERE hidden=0 AND junk=0 GROUP BY host ORDER BY n DESC").fetchall()
        return render_template("browse.html", active="browse", hosts=hosts, crumbs=[], dirs=[], files=[])
    if not p.endswith("/"):
        p += "/"
    rows = db.execute("SELECT canon, COUNT(*) n, MIN(ts) a, MAX(ts) b, MAX(title) title FROM captures "
                      "WHERE hidden=0 AND junk=0 AND canon_lc LIKE ? ESCAPE '\\' GROUP BY canon",
                      (like_prefix(p),)).fetchall()
    dirs, files, grouped = {}, [], {}
    for r in rows:
        rest = r["canon"][len(p):]
        rest_path, _, query = rest.partition("?")
        if "/" in rest_path:
            name = rest_path.split("/")[0]
            d = dirs.setdefault(name, {"label": name, "n": 0, "caps": 0, "a": r["a"], "b": r["b"],
                                       "href": url_for("browse", p=p + name + "/")})
            d["n"] += 1
            d["caps"] += r["n"]
            d["a"], d["b"] = min(d["a"], r["a"]), max(d["b"], r["b"])
            continue
        if variant and rest_path != variant:
            continue
        if query and not variant:
            grp = grouped.setdefault(rest_path, {"label": f"{rest_path or '(첫 페이지)'}?…", "n": 0, "a": r["a"],
                                                 "b": r["b"], "title": f"주소 뒤 변형 0개",
                                                 "href": url_for("browse", p=p, f=rest_path), "variants": 0})
            grp["variants"] += 1
            grp["n"] += r["n"]
            grp["a"], grp["b"] = min(grp["a"], r["a"]), max(grp["b"], r["b"])
            grp["title"] = f"주소 뒤 변형 {grp['variants']}개"
            continue
        files.append({"label": (rest or "(첫 페이지)"), "n": r["n"], "a": r["a"], "b": r["b"], "title": r["title"],
                      "href": ac.view_href(r["b"], r["canon"])})
    files = sorted(files, key=lambda f: f["label"].lower()) + sorted(grouped.values(), key=lambda f: f["label"].lower())
    total_files = len(files)
    files = files[:BROWSE_LIMIT]
    parts = p.strip("/").split("/")
    crumbs = [(parts[i], "/".join(parts[:i + 1]) + "/") for i in range(len(parts))]
    return render_template("browse.html", active="browse", hosts=None, crumbs=crumbs, variant=variant,
                           dirs=sorted(dirs.values(), key=lambda d: d["label"].lower()), files=files,
                           total_files=total_files, limit=BROWSE_LIMIT)


# ------------------------------------------------------------------ 보기·원본
def clean_ts(ts):
    return re.sub(r"\D", "", ts)[:14] or "2003"


@app.route("/view/<ts>/<path:target>")
def view(ts, target):
    ts = clean_ts(ts)
    want = ac.canon(target)
    db = get_db()
    cap, tier, hidden_hit = ac.find_capture(db, want, ts, CFG)
    if not cap:
        return render_template("view.html", cap=None, want=want, ts=ts, hidden_hit=hidden_hit), 404
    rows = db.execute("SELECT ts FROM captures WHERE canon=? AND hidden=0 AND junk=0 GROUP BY ts ORDER BY ts",
                      (cap["canon"],)).fetchall()
    eps = [ac.ts_epoch(r["ts"]) for r in rows]
    lo, hi = min(eps), max(eps)
    versions = [{"ts": r["ts"], "href": ac.view_href(r["ts"], cap["canon"]),
                 "pos": 50.0 if hi == lo else round(2 + (e - lo) / (hi - lo) * 96, 2)} for r, e in zip(rows, eps)]
    idx = next(i for i, v in enumerate(versions) if v["ts"] == cap["ts"])
    return render_template(
        "view.html", cap=cap, tier=tier, versions=versions, raw=ac.raw_href(cap["ts"], cap["canon"]),
        prev_href=versions[idx - 1]["href"] if idx > 0 else None,
        next_href=versions[idx + 1]["href"] if idx + 1 < len(versions) else None)


MISSING_HTML = """<!doctype html><meta charset="utf-8"><title>없음</title>
<body style="font:15px/1.6 sans-serif;color:#332000;background:#c0c0c0;padding:1.5rem">
<p><b>{msg}</b></p><p style="font-family:monospace;word-break:break-all">{url}</p>
<p><a href="https://web.archive.org/web/{ts}/{url}" target="_blank" rel="noopener noreferrer">웨이백에서 찾아보기</a></p></body>"""


@app.route("/raw/<ts>/<path:target>")
def raw(ts, target):
    ts = clean_ts(ts)
    want = ac.canon(target)
    cap, _, hidden_hit = ac.find_capture(get_db(), want, ts, CFG)
    if not cap:
        msg = "이 사이트에서 보이지 않게 설정된 자료입니다." if hidden_hit else "받은 자료에 없는 주소입니다."
        resp = Response(MISSING_HTML.format(msg=msg, url=escape(want), ts=ts), 404, mimetype="text/html")
        resp.headers["Content-Security-Policy"] = RAW_CSP
        return resp
    return serve_capture(cap)


def serve_capture(cap, page=True):
    path = file_path(cap)
    if page and (cap["kind"] == "page" or cap["sniff"] == "html"):
        with open(path, "rb") as f:
            text, codec = ac.decode_page(f.read())
        resp = Response(ac.rewrite_html(text, cap["url"], cap["ts"], codec), mimetype="text/html")
        resp.headers["Content-Security-Policy"] = RAW_CSP
        resp.headers["Cache-Control"] = "private, max-age=3600"
        return resp
    attach = cap["kind"] in ("archive", "program") or cap["mime"] == "application/octet-stream"
    resp = send_file(path, mimetype=cap["mime"], conditional=True, as_attachment=attach,
                     download_name=cap["name"] or "file", max_age=86400)
    resp.headers["Content-Security-Policy"] = RAW_CSP
    return resp


def visible_capture(cap_id):
    cap = get_db().execute("SELECT * FROM captures WHERE id=? AND hidden=0 AND junk=0", (cap_id,)).fetchone()
    if not cap:
        abort(404)
    return cap


@app.route("/file/<int:cap_id>")
def file(cap_id):
    cap = visible_capture(cap_id)
    path = file_path(cap)
    resp = send_file(path, mimetype="application/octet-stream" if cap["kind"] == "page" else cap["mime"],
                     conditional=True, as_attachment=cap["kind"] in ("page", "archive", "program", "other"),
                     download_name=cap["name"] or "file", max_age=86400)
    resp.headers["Content-Security-Policy"] = RAW_CSP
    return resp


@app.route("/derived/<int:cap_id>")
def derived(cap_id):
    cap = visible_capture(cap_id)
    if not cap["derived"]:
        abort(404)
    full = os.path.realpath(os.path.join(DATA_DIR, cap["derived"]))
    if not full.startswith(DATA_DIR + os.sep) or not os.path.isfile(full):
        abort(404)
    return send_file(full, conditional=True, max_age=86400)


# ------------------------------------------------------------------ 사진첩·미디어
@app.route("/gallery")
def gallery():
    rows = get_db().execute("SELECT * FROM albums ORDER BY date = '', date, title").fetchall()
    groups = {}
    for a in rows:
        groups.setdefault(a["date"][:4] or "날짜를 알 수 없는 사진첩", []).append(a)
    return render_template("gallery.html", active="gallery", groups=list(groups.items()))


@app.route("/album/<int:album_id>")
def album(album_id):
    db = get_db()
    a = db.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone()
    if not a:
        abort(404)
    photos = db.execute(
        "SELECT c.id, c.canon, c.ts, c.name FROM captures c WHERE c.dir=? AND c.kind='image' AND c.hidden=0 "
        "AND c.junk=0 AND c.size>6000 AND c.ts=(SELECT MAX(ts) FROM captures c2 WHERE c2.canon=c.canon "
        "AND c2.hidden=0 AND c2.junk=0) GROUP BY c.canon ORDER BY lower(c.name)", (a["dir"],)).fetchall()
    return render_template("album.html", active="gallery", album=a, photos=photos)


@app.route("/media")
def media():
    rows = get_db().execute(
        "SELECT c.* FROM captures c WHERE c.kind IN ('video','audio') AND c.hidden=0 AND c.junk=0 AND "
        "c.ts=(SELECT MAX(ts) FROM captures c2 WHERE c2.canon=c.canon AND c2.hidden=0 AND c2.junk=0) "
        "GROUP BY c.canon ORDER BY c.section, lower(c.name)").fetchall()
    items, not_playable = [], False
    for r in rows:
        m = dict(r)
        if r["derived"]:
            m["play_src"] = url_for("derived", cap_id=r["id"])
        elif r["ext"] in ac.BROWSER_PLAYABLE:
            m["play_src"] = url_for("raw_file", cap_id=r["id"])
        else:
            m["play_src"] = None
            not_playable = True
        items.append(m)
    return render_template("media.html", active="media", items=items, not_playable=not_playable)


@app.route("/raw-file/<int:cap_id>")
def raw_file(cap_id):
    """재생용 (내려받기 창을 띄우지 않음)"""
    return serve_capture(visible_capture(cap_id), page=False)


# ------------------------------------------------------------------ 찾기
def make_snippet(body, q, width=70):
    i = body.lower().find(q.lower())
    if i < 0:
        return escape(body[:160]) + ("…" if len(body) > 160 else "")
    a, b = max(0, i - width), min(len(body), i + len(q) + width)
    return Markup("{}{}<mark>{}</mark>{}{}").format("…" if a else "", body[a:i], body[i:i + len(q)],
                                                     body[i + len(q):b], "…" if b < len(body) else "")


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()[:100]
    section = request.args.get("section", "")
    page = max(1, request.args.get("page", 1, type=int))
    ctx = {"active": "search", "q": q, "section": section, "sections": meta().get("sections", []),
           "results": [], "total": 0, "page": page, "pages": 0, "limit": SEARCH_LIMIT}
    if not q:
        return render_template("search.html", **ctx)
    db = get_db()
    where_sec = " AND c.section=?" if section else ""
    args_sec = [section] if section else []
    if meta().get("fts") and len(q) >= 3:
        phrase = '"' + q.replace('"', '""') + '"'
        rows = db.execute("SELECT c.id, c.canon, c.ts, c.title FROM page_fts f JOIN captures c ON c.id=f.rowid "
                          f"WHERE page_fts MATCH ? AND c.hidden=0 AND c.junk=0{where_sec} LIMIT 3000",
                          [phrase] + args_sec).fetchall()
    else:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = db.execute("SELECT c.id, c.canon, c.ts, c.title FROM captures c JOIN page_text t ON t.id=c.id "
                          f"WHERE (t.body LIKE ? ESCAPE '\\' OR c.title LIKE ? ESCAPE '\\') AND c.hidden=0 "
                          f"AND c.junk=0{where_sec} LIMIT 3000", [like, like] + args_sec).fetchall()
    best = {}
    for r in rows:                              # 같은 주소는 가장 최근 캡처 하나로
        b = best.get(r["canon"])
        if not b or r["ts"] > b["ts"]:
            best[r["canon"]] = {"id": r["id"], "canon": r["canon"], "ts": r["ts"], "title": r["title"],
                                "n": (b["n"] if b else 0) + 1}
        else:
            b["n"] += 1
    ordered = sorted(best.values(), key=lambda x: (0 if q.lower() in (x["title"] or "").lower() else 1, x["canon"]))
    ordered = ordered[:SEARCH_LIMIT]
    pages = max(1, math.ceil(len(ordered) / PER_PAGE))
    chunk = ordered[(page - 1) * PER_PAGE: page * PER_PAGE]
    for r in chunk:
        body = db.execute("SELECT body FROM page_text WHERE id=?", (r["id"],)).fetchone()
        r["snippet"] = make_snippet(body["body"] if body else "", q)
    ctx.update(results=chunk, total=len(ordered), pages=pages)
    return render_template("search.html", **ctx)


@app.errorhandler(413)
def too_large(_e):
    return Response("올린 파일이 너무 큽니다.", 413)


@app.errorhandler(403)
def forbidden(_e):
    return render_template("message.html", active=None, title="권한이 없습니다",
                           text="이 작업을 할 권한이 없습니다. 로그인했는지 확인하세요."), 403


@app.errorhandler(404)
def not_found(_e):
    if request.path.startswith(("/raw/", "/file/", "/derived/", "/raw-file/")):
        return Response("없음", 404)
    return render_template("view.html", cap=None, want=request.path, ts="2003", hidden_hit=False), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"YSOYA_Archive: http://127.0.0.1:{port}  (끄려면 Ctrl+C)")
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=port, debug=False)
