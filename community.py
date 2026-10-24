# -*- coding: utf-8 -*-
"""
YSOYA_Archive 커뮤니티 (게시판, 회원, 첨부 파일, 자료실, 인물 페이지)

- 회원 정보는 닉네임과 암호화한 비밀번호만 저장 (이메일·실명·IP 저장 안 함)
- 가입 방식(site_config.json 의 community.signup): invite(초대 코드) / open(누구나) / closed(막음)
- 데이터: DATA_DIR/community.db (SQLite), 첨부 파일: DATA_DIR/uploads/
- 첫 관리자 만들기:  flask --app app create-admin 닉네임
"""
import json
import math
import os
import re
import secrets
import sqlite3
import time
import uuid

import click
from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file,
                   session, url_for)
from markupsafe import Markup, escape
from werkzeug.security import check_password_hash, generate_password_hash

import archive_core as ac

bp = Blueprint("community", __name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT UNIQUE COLLATE NOCASE, pw TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'member', banned INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS invites (code TEXT PRIMARY KEY, uses_left INTEGER NOT NULL, created_by INTEGER,
  created INTEGER NOT NULL, note TEXT);
CREATE TABLE IF NOT EXISTS posts (id INTEGER PRIMARY KEY, board TEXT NOT NULL, user_id INTEGER NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0,
  views INTEGER NOT NULL DEFAULT 0, comments INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL, updated INTEGER);
CREATE INDEX IF NOT EXISTS ix_posts_board ON posts(board, hidden, pinned DESC, id DESC);
CREATE TABLE IF NOT EXISTS comments (id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  body TEXT NOT NULL, hidden INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_comments_post ON comments(post_id, id);
CREATE TABLE IF NOT EXISTS attachments (id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  name TEXT NOT NULL, stored TEXT NOT NULL, size INTEGER NOT NULL, ext TEXT NOT NULL, kind TEXT NOT NULL,
  created INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_att_post ON attachments(post_id);
CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY, target TEXT NOT NULL, target_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL, reason TEXT, created INTEGER NOT NULL, resolved INTEGER NOT NULL DEFAULT 0);
"""
NAME_RE = re.compile(r"^[0-9A-Za-z가-힣_\-]{2,20}$")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
IMAGE_SNIFF = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".gif": "gif"}
LIMITS = {"title": 120, "body": 20000, "comment": 2000, "reason": 300}
WAIT = {"post": 20, "comment": 8}          # 같은 사람이 연달아 쓸 때 기다릴 초
PER_PAGE = 20
DOC_EXTS = {".txt", ".pdf", ".doc", ".hwp", ".xls", ".ppt", ".smi", ".srt", ".lzh", ".alz", ".rar", ".7z"}

_CFG = {}
_CAREER = {"mtime": None, "data": None}


def init_app(app, cfg, data_dir, base_dir, archive_db):
    _CFG.update(cfg=cfg, data=data_dir, base=base_dir, db=os.path.join(data_dir, "community.db"),
                uploads=os.path.join(data_dir, "uploads"), archive_db=archive_db)
    os.makedirs(_CFG["uploads"], exist_ok=True)
    con = sqlite3.connect(_CFG["db"])
    mode = os.environ.get("SQLITE_JOURNAL", "WAL").upper()
    con.execute(f"PRAGMA journal_mode={mode if mode in ('WAL', 'DELETE', 'TRUNCATE') else 'WAL'}")
    con.executescript(SCHEMA)
    con.close()
    app.register_blueprint(bp)
    app.cli.add_command(create_admin)
    app.cli.add_command(reset_password)


def ccfg():
    return _CFG["cfg"].get("community", {})


def boards():
    return ccfg().get("boards", [])


def board_by_slug(slug):
    return next((b for b in boards() if b["slug"] == slug), None)


def cdb():
    if "cdb" not in g:
        g.cdb = sqlite3.connect(_CFG["db"], timeout=10)
        g.cdb.row_factory = sqlite3.Row
        g.cdb.execute("PRAGMA foreign_keys=ON")
    return g.cdb


@bp.teardown_app_request
def _close(_exc):
    con = g.pop("cdb", None)
    if con is not None:
        con.close()


# ------------------------------------------------------------------ 로그인 상태·보안 도우미
@bp.before_app_request
def load_user():
    g.user = None
    uid = session.get("uid")
    if uid:
        u = cdb().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if u and not u["banned"]:
            g.user = u
        else:
            session.pop("uid", None)


def is_admin():
    return bool(g.user and g.user["role"] == "admin")


def require_member():
    if not g.user:
        flash("로그인이 필요합니다.")
        return redirect(url_for("community.login", next=request.full_path))
    return None


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def csrf_ok():
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
    return bool(session.get("csrf")) and secrets.compare_digest(sent, session["csrf"])


def too_soon(kind):
    table = "posts" if kind == "post" else "comments"
    last = cdb().execute(f"SELECT MAX(created) FROM {table} WHERE user_id=?", (g.user["id"],)).fetchone()[0] or 0
    left = WAIT[kind] - (int(time.time()) - last)
    return left if left > 0 and not is_admin() else 0


URL_RE = re.compile(r"https?://[^\s<>\"']+")


def render_body(text):
    """글 본문: HTML 은 모두 글자로 바꾸고, 주소만 링크로. 줄바꿈 유지."""
    out = []
    for line in text.split("\n"):
        esc = str(escape(line))

        def link(m):
            url = m.group(0)
            tail = ""
            while url and (url[-1] in ".,)]!?" or url.endswith(("&#34;", "&#39;"))):
                cut = 5 if url.endswith(("&#34;", "&#39;")) else 1
                tail = url[-cut:] + tail
                url = url[:-cut]
            return f'<a href="{url}" rel="nofollow noopener ugc" target="_blank">{url}</a>{tail}'
        out.append(URL_RE.sub(link, esc))
    return Markup("<br>".join(out))


def fmt_time(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""


def fmt_size(n):
    return f"{n / 1048576:.1f}MB" if n >= 1048576 else f"{max(1, n // 1024)}KB"


@bp.app_context_processor
def inject():
    return {"me": g.get("user"), "is_admin": is_admin(), "csrf_token": csrf_token, "render_body": render_body,
            "fmt_time": fmt_time, "fmt_size": fmt_size, "boards": boards()}


# ------------------------------------------------------------------ 회원
@bp.route("/account/join", methods=["GET", "POST"])
def join():
    mode = ccfg().get("signup", "invite")
    if mode == "closed":
        return render_template("account_join.html", active=None, closed=True, mode=mode)
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        pw = request.form.get("password", "")
        code = request.form.get("invite", "").strip()
        db = cdb()
        if not NAME_RE.match(name):
            error = "닉네임은 2~20자의 한글, 영문, 숫자, _ - 만 쓸 수 있습니다."
        elif len(pw) < 8:
            error = "비밀번호는 8자 이상으로 정해 주세요."
        elif pw != request.form.get("password2", ""):
            error = "비밀번호 두 칸이 서로 다릅니다."
        elif db.execute("SELECT 1 FROM users WHERE name=?", (name,)).fetchone():
            error = "이미 쓰이는 닉네임입니다."
        elif mode == "invite" and not db.execute("SELECT 1 FROM invites WHERE code=? AND uses_left>0", (code,)).fetchone():
            error = "초대 코드가 맞지 않거나 다 쓰였습니다."
        if not error:
            with db:
                if mode == "invite":
                    db.execute("UPDATE invites SET uses_left=uses_left-1 WHERE code=?", (code,))
                cur = db.execute("INSERT INTO users (name, pw, created) VALUES (?,?,?)",
                                 (name, generate_password_hash(pw), int(time.time())))
            session.clear()
            session["uid"] = cur.lastrowid
            session.permanent = True
            flash(f"{name}님, 가입을 환영합니다.")
            return redirect(url_for("community.board_index"))
    return render_template("account_join.html", active=None, closed=False, mode=mode, error=error)


_FAILS = {}


@bp.route("/account/login", methods=["GET", "POST"])
def login():
    nxt = request.values.get("next", "/")
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        key = (request.remote_addr or "?") + "|" + name.lower()
        if _FAILS.get(key, 0) >= 5:
            time.sleep(2)
        u = cdb().execute("SELECT * FROM users WHERE name=?", (name,)).fetchone()
        if u and check_password_hash(u["pw"], request.form.get("password", "")):
            if u["banned"]:
                error = "이용이 정지된 계정입니다."
            else:
                _FAILS.pop(key, None)
                site_ok = session.get("ok")
                session.clear()                 # 로그인할 때 세션을 새로 (세션 고정 공격 방지)
                if site_ok:
                    session["ok"] = True
                session["uid"] = u["id"]
                session.permanent = True
                return redirect(nxt)
        else:
            _FAILS[key] = _FAILS.get(key, 0) + 1
            error = "닉네임이나 비밀번호가 맞지 않습니다."
    return render_template("account_login.html", active=None, error=error, next=nxt,
                           signup=ccfg().get("signup", "invite"))


@bp.route("/account/logout", methods=["POST"])
def logout():
    site_ok = session.get("ok")
    session.clear()
    if site_ok:
        session["ok"] = True
    return redirect(url_for("home"))


@bp.route("/account", methods=["GET", "POST"])
def account():
    need = require_member()
    if need:
        return need
    error = None
    if request.method == "POST":
        if not check_password_hash(g.user["pw"], request.form.get("current", "")):
            error = "지금 비밀번호가 맞지 않습니다."
        elif len(request.form.get("password", "")) < 8:
            error = "새 비밀번호는 8자 이상으로 정해 주세요."
        elif request.form.get("password") != request.form.get("password2"):
            error = "새 비밀번호 두 칸이 서로 다릅니다."
        else:
            with cdb() as db:
                db.execute("UPDATE users SET pw=? WHERE id=?", (generate_password_hash(request.form["password"]), g.user["id"]))
            flash("비밀번호를 바꿨습니다.")
            return redirect(url_for("community.account"))
    posts = cdb().execute("SELECT id, board, title, created FROM posts WHERE user_id=? AND hidden=0 ORDER BY id DESC LIMIT 20",
                          (g.user["id"],)).fetchall()
    return render_template("account.html", active=None, error=error, posts=posts)


# ------------------------------------------------------------------ 게시판
def can_write(board):
    return bool(g.user) and (not board.get("admin_only") or is_admin())


@bp.route("/board")
def board_index():
    db = cdb()
    items = []
    for b in boards():
        latest = db.execute("SELECT p.id, p.title, p.created, p.comments, u.name FROM posts p JOIN users u ON u.id=p.user_id "
                            "WHERE p.board=? AND p.hidden=0 ORDER BY p.pinned DESC, p.id DESC LIMIT 5", (b["slug"],)).fetchall()
        count = db.execute("SELECT COUNT(*) FROM posts WHERE board=? AND hidden=0", (b["slug"],)).fetchone()[0]
        items.append({"board": b, "latest": latest, "count": count})
    return render_template("board_index.html", active="board", items=items)


@bp.route("/board/<slug>")
def board(slug):
    b = board_by_slug(slug) or abort(404)
    q = request.args.get("q", "").strip()[:50]
    page = max(1, request.args.get("page", 1, type=int))
    where, args = "p.board=? AND p.hidden=0", [slug]
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where += " AND (p.title LIKE ? ESCAPE '\\' OR p.body LIKE ? ESCAPE '\\')"
        args += [like, like]
    db = cdb()
    total = db.execute(f"SELECT COUNT(*) FROM posts p WHERE {where}", args).fetchone()[0]
    pages = max(1, math.ceil(total / PER_PAGE))
    rows = db.execute(f"SELECT p.*, u.name, (SELECT COUNT(*) FROM attachments a WHERE a.post_id=p.id) files "
                      f"FROM posts p JOIN users u ON u.id=p.user_id WHERE {where} "
                      f"ORDER BY p.pinned DESC, p.id DESC LIMIT ? OFFSET ?", args + [PER_PAGE, (page - 1) * PER_PAGE]).fetchall()
    return render_template("board.html", active="board", b=b, posts=rows, q=q, page=page, pages=pages, total=total,
                           can_write=can_write(b))


def save_uploads(post_id, files):
    """첨부 파일 저장. 문제 있는 파일은 건너뛰고 이유를 돌려준다."""
    c = ccfg()
    allowed = {e.lower() for e in c.get("allowed_exts", [])}
    max_bytes = int(c.get("max_upload_mb", 20)) * 1048576
    room = int(c.get("max_files_per_post", 5)) - cdb().execute(
        "SELECT COUNT(*) FROM attachments WHERE post_id=?", (post_id,)).fetchone()[0]
    problems = []
    for f in files:
        if not f or not f.filename:
            continue
        name = os.path.basename(f.filename.replace("\\", "/"))[:120] or "file"
        ext = os.path.splitext(name)[1].lower()
        if room <= 0:
            problems.append(f"{name}: 글 하나에 붙일 수 있는 파일 수를 넘었습니다")
            continue
        if ext not in allowed:
            problems.append(f"{name}: 올릴 수 없는 형식입니다")
            continue
        data = f.read(max_bytes + 1)
        if len(data) > max_bytes:
            problems.append(f"{name}: {c.get('max_upload_mb', 20)}MB 를 넘습니다")
            continue
        if not data:
            problems.append(f"{name}: 빈 파일입니다")
            continue
        if ext in IMAGE_SNIFF and ac.sniff(data[:64]) != IMAGE_SNIFF[ext]:
            problems.append(f"{name}: 이름은 그림인데 내용이 그림이 아닙니다")
            continue
        if ext == ".webp" and not (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
            problems.append(f"{name}: 이름은 그림인데 내용이 그림이 아닙니다")
            continue
        stored = uuid.uuid4().hex + ext
        sub = os.path.join(_CFG["uploads"], stored[:2])
        os.makedirs(sub, exist_ok=True)
        with open(os.path.join(sub, stored), "wb") as out:
            out.write(data)
        kind = "image" if ext in IMAGE_EXTS else "file"
        with cdb() as db:
            db.execute("INSERT INTO attachments (post_id, user_id, name, stored, size, ext, kind, created) VALUES (?,?,?,?,?,?,?,?)",
                       (post_id, g.user["id"], name, stored, len(data), ext, kind, int(time.time())))
        room -= 1
    return problems


@bp.route("/board/<slug>/write", methods=["GET", "POST"])
def write(slug):
    b = board_by_slug(slug) or abort(404)
    need = require_member()
    if need:
        return need
    if not can_write(b):
        abort(403)
    error = None
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    if request.method == "POST":
        wait = too_soon("post")
        if not title or not body:
            error = "제목과 내용을 모두 적어 주세요."
        elif len(title) > LIMITS["title"] or len(body) > LIMITS["body"]:
            error = f"제목은 {LIMITS['title']}자, 내용은 {LIMITS['body']:,}자까지 쓸 수 있습니다."
        elif wait:
            error = f"방금 글을 썼습니다. {wait}초 뒤에 다시 시도해 주세요."
        else:
            now = int(time.time())
            with cdb() as db:
                cur = db.execute("INSERT INTO posts (board, user_id, title, body, created) VALUES (?,?,?,?,?)",
                                 (slug, g.user["id"], title, body, now))
            for p in save_uploads(cur.lastrowid, request.files.getlist("files")):
                flash(p)
            return redirect(url_for("community.post", post_id=cur.lastrowid))
    return render_template("post_form.html", active="board", b=b, title=title, body=body, error=error, post=None,
                           atts=[], c=ccfg())


def get_post(post_id, allow_hidden=False):
    p = cdb().execute("SELECT p.*, u.name FROM posts p JOIN users u ON u.id=p.user_id WHERE p.id=?", (post_id,)).fetchone()
    if not p or (p["hidden"] and not (allow_hidden and is_admin())):
        abort(404)
    return p


def can_edit(p):
    return bool(g.user) and (g.user["id"] == p["user_id"] or is_admin())


@bp.route("/post/<int:post_id>")
def post(post_id):
    p = get_post(post_id, allow_hidden=True)
    seen = session.get("seen", [])
    if post_id not in seen:                         # 같은 사람이 새로 고침할 때마다 늘지 않게
        with cdb() as db:
            db.execute("UPDATE posts SET views=views+1 WHERE id=?", (post_id,))
        session["seen"] = (seen + [post_id])[-200:]
    db = cdb()
    comments = db.execute("SELECT c.*, u.name FROM comments c JOIN users u ON u.id=c.user_id WHERE c.post_id=? "
                          "ORDER BY c.id", (post_id,)).fetchall()
    atts = db.execute("SELECT * FROM attachments WHERE post_id=? ORDER BY id", (post_id,)).fetchall()
    return render_template("post.html", active="board", p=p, b=board_by_slug(p["board"]) or {"name": p["board"], "slug": p["board"]},
                           comments=comments, atts=atts, can_edit=can_edit(p))


@bp.route("/post/<int:post_id>/edit", methods=["GET", "POST"])
def edit(post_id):
    need = require_member()
    if need:
        return need
    p = get_post(post_id)
    if not can_edit(p):
        abort(403)
    error = None
    title, body = p["title"], p["body"]
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()
        if not title or not body:
            error = "제목과 내용을 모두 적어 주세요."
        elif len(title) > LIMITS["title"] or len(body) > LIMITS["body"]:
            error = f"제목은 {LIMITS['title']}자, 내용은 {LIMITS['body']:,}자까지 쓸 수 있습니다."
        else:
            with cdb() as db:
                db.execute("UPDATE posts SET title=?, body=?, updated=? WHERE id=?", (title, body, int(time.time()), post_id))
                for att_id in request.form.getlist("remove_att"):
                    remove_attachment(db, int(att_id), post_id)
            for prob in save_uploads(post_id, request.files.getlist("files")):
                flash(prob)
            return redirect(url_for("community.post", post_id=post_id))
    atts = cdb().execute("SELECT * FROM attachments WHERE post_id=?", (post_id,)).fetchall()
    return render_template("post_form.html", active="board", b=board_by_slug(p["board"]), title=title, body=body,
                           error=error, post=p, atts=atts, c=ccfg())


def remove_attachment(db, att_id, post_id):
    a = db.execute("SELECT * FROM attachments WHERE id=? AND post_id=?", (att_id, post_id)).fetchone()
    if a:
        db.execute("DELETE FROM attachments WHERE id=?", (att_id,))
        try:
            os.remove(os.path.join(_CFG["uploads"], a["stored"][:2], a["stored"]))
        except OSError:
            pass


@bp.route("/post/<int:post_id>/delete", methods=["POST"])
def delete(post_id):
    need = require_member()
    if need:
        return need
    p = get_post(post_id)
    if not can_edit(p):
        abort(403)
    with cdb() as db:
        db.execute("UPDATE posts SET hidden=1 WHERE id=?", (post_id,))     # 지우지 않고 숨김 (관리자가 되살릴 수 있음)
    flash("글을 지웠습니다.")
    return redirect(url_for("community.board", slug=p["board"]))


@bp.route("/post/<int:post_id>/pin", methods=["POST"])
def pin(post_id):
    if not is_admin():
        abort(403)
    p = get_post(post_id)
    with cdb() as db:
        db.execute("UPDATE posts SET pinned=? WHERE id=?", (0 if p["pinned"] else 1, post_id))
    return redirect(url_for("community.post", post_id=post_id))


@bp.route("/post/<int:post_id>/comment", methods=["POST"])
def comment(post_id):
    need = require_member()
    if need:
        return need
    get_post(post_id)
    body = request.form.get("body", "").strip()
    wait = too_soon("comment")
    if not body:
        flash("댓글 내용을 적어 주세요.")
    elif len(body) > LIMITS["comment"]:
        flash(f"댓글은 {LIMITS['comment']:,}자까지 쓸 수 있습니다.")
    elif wait:
        flash(f"방금 댓글을 썼습니다. {wait}초 뒤에 다시 시도해 주세요.")
    else:
        with cdb() as db:
            db.execute("INSERT INTO comments (post_id, user_id, body, created) VALUES (?,?,?,?)",
                       (post_id, g.user["id"], body, int(time.time())))
            db.execute("UPDATE posts SET comments=(SELECT COUNT(*) FROM comments WHERE post_id=? AND hidden=0) WHERE id=?",
                       (post_id, post_id))
    return redirect(url_for("community.post", post_id=post_id) + "#comments")


@bp.route("/comment/<int:cid>/delete", methods=["POST"])
def delete_comment(cid):
    need = require_member()
    if need:
        return need
    c = cdb().execute("SELECT * FROM comments WHERE id=?", (cid,)).fetchone() or abort(404)
    if not (g.user["id"] == c["user_id"] or is_admin()):
        abort(403)
    with cdb() as db:
        db.execute("UPDATE comments SET hidden=1 WHERE id=?", (cid,))
        db.execute("UPDATE posts SET comments=(SELECT COUNT(*) FROM comments WHERE post_id=? AND hidden=0) WHERE id=?",
                   (c["post_id"], c["post_id"]))
    return redirect(url_for("community.post", post_id=c["post_id"]) + "#comments")


@bp.route("/report/<target>/<int:tid>", methods=["POST"])
def report(target, tid):
    need = require_member()
    if need:
        return need
    if target not in ("post", "comment"):
        abort(404)
    reason = request.form.get("reason", "").strip()[:LIMITS["reason"]] or "(이유 없음)"
    with cdb() as db:
        db.execute("INSERT INTO reports (target, target_id, user_id, reason, created) VALUES (?,?,?,?,?)",
                   (target, tid, g.user["id"], reason, int(time.time())))
    flash("신고를 받았습니다. 관리자가 확인합니다.")
    back = tid if target == "post" else (cdb().execute("SELECT post_id FROM comments WHERE id=?", (tid,)).fetchone() or [0])[0]
    return redirect(url_for("community.post", post_id=back) if back else url_for("community.board_index"))


@bp.route("/u/<int:att_id>")
def attachment(att_id):
    a = cdb().execute("SELECT a.*, p.hidden FROM attachments a JOIN posts p ON p.id=a.post_id WHERE a.id=?", (att_id,)).fetchone()
    if not a or (a["hidden"] and not is_admin()):
        abort(404)
    path = os.path.join(_CFG["uploads"], a["stored"][:2], a["stored"])
    if not os.path.isfile(path):
        abort(404)
    inline = a["kind"] == "image"
    resp = send_file(path, mimetype=ac.MIME.get(a["ext"], "image/webp" if a["ext"] == ".webp" else "application/octet-stream"),
                     as_attachment=not inline, download_name=a["name"], conditional=True, max_age=86400)
    resp.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
    return resp


# ------------------------------------------------------------------ 관리
@bp.route("/admin", methods=["GET", "POST"])
def admin():
    if not is_admin():
        abort(403)
    db = cdb()
    if request.method == "POST":
        act = request.form.get("action")
        with db:
            if act == "invite":
                code = secrets.token_urlsafe(9)
                uses = max(1, min(100, request.form.get("uses", 1, type=int)))
                db.execute("INSERT INTO invites (code, uses_left, created_by, created, note) VALUES (?,?,?,?,?)",
                           (code, uses, g.user["id"], int(time.time()), request.form.get("note", "")[:100]))
                flash(f"초대 코드를 만들었습니다: {code}")
            elif act == "delete_invite":
                db.execute("DELETE FROM invites WHERE code=?", (request.form.get("code"),))
            elif act in ("ban", "unban"):
                uid = request.form.get("uid", type=int)
                if uid and uid != g.user["id"]:
                    db.execute("UPDATE users SET banned=? WHERE id=?", (1 if act == "ban" else 0, uid))
            elif act == "resolve":
                db.execute("UPDATE reports SET resolved=1 WHERE id=?", (request.form.get("rid", type=int),))
            elif act == "hide_target":
                r = db.execute("SELECT * FROM reports WHERE id=?", (request.form.get("rid", type=int),)).fetchone()
                if r:
                    table = "posts" if r["target"] == "post" else "comments"
                    db.execute(f"UPDATE {table} SET hidden=1 WHERE id=?", (r["target_id"],))
                    db.execute("UPDATE reports SET resolved=1 WHERE id=?", (r["id"],))
            elif act == "restore":
                db.execute("UPDATE posts SET hidden=0 WHERE id=?", (request.form.get("pid", type=int),))
        return redirect(url_for("community.admin"))
    reports = db.execute("SELECT r.*, u.name FROM reports r JOIN users u ON u.id=r.user_id WHERE r.resolved=0 ORDER BY r.id DESC").fetchall()
    invites = db.execute("SELECT * FROM invites ORDER BY created DESC").fetchall()
    users = db.execute("SELECT u.*, (SELECT COUNT(*) FROM posts p WHERE p.user_id=u.id) posts FROM users u ORDER BY u.id DESC LIMIT 200").fetchall()
    hidden = db.execute("SELECT p.id, p.title, p.board, u.name FROM posts p JOIN users u ON u.id=p.user_id WHERE p.hidden=1 ORDER BY p.id DESC LIMIT 50").fetchall()
    return render_template("admin.html", active=None, reports=reports, invites=invites, users=users, hidden=hidden)


@click.command("create-admin")
@click.argument("name")
@click.password_option("--password", prompt="비밀번호", confirmation_prompt="비밀번호 확인")
def create_admin(name, password):
    """관리자 계정을 만들거나, 있는 회원을 관리자로 올린다."""
    if not NAME_RE.match(name):
        raise click.ClickException("닉네임은 2~20자의 한글, 영문, 숫자, _ - 만 쓸 수 있습니다.")
    if len(password) < 8:
        raise click.ClickException("비밀번호는 8자 이상으로 정해 주세요.")
    con = sqlite3.connect(_CFG["db"])
    with con:
        row = con.execute("SELECT id FROM users WHERE name=?", (name,)).fetchone()
        if row:
            con.execute("UPDATE users SET role='admin', pw=?, banned=0 WHERE id=?", (generate_password_hash(password), row[0]))
        else:
            con.execute("INSERT INTO users (name, pw, role, created) VALUES (?,?, 'admin', ?)",
                        (name, generate_password_hash(password), int(time.time())))
    con.close()
    click.echo(f"관리자 '{name}' 준비 완료")


@click.command("reset-password")
@click.argument("name")
@click.password_option("--password", prompt="새 비밀번호", confirmation_prompt="새 비밀번호 확인")
def reset_password(name, password):
    """비밀번호를 잊은 회원의 비밀번호를 새로 정한다 (역할은 그대로)."""
    if len(password) < 8:
        raise click.ClickException("비밀번호는 8자 이상으로 정해 주세요.")
    con = sqlite3.connect(_CFG["db"])
    with con:
        n = con.execute("UPDATE users SET pw=? WHERE name=?", (generate_password_hash(password), name)).rowcount
    con.close()
    if not n:
        raise click.ClickException(f"'{name}' 회원이 없습니다.")
    click.echo(f"'{name}' 의 비밀번호를 바꿨습니다")


# ------------------------------------------------------------------ 자료실·인물
@bp.route("/files")
def files():
    get_db = _CFG["archive_db"]          # 보존된 자료 색인 (app.py 의 get_db)
    kinds = {"archive": "압축 파일", "audio": "음악·음성", "video": "영상", "playlist": "재생목록", "doc": "문서·기타"}
    rows = get_db().execute(
        "SELECT c.* FROM captures c WHERE c.hidden=0 AND c.junk=0 AND (c.kind IN ('archive','audio','video','playlist') "
        f"OR c.ext IN ({','.join('?' * len(DOC_EXTS))})) AND c.ts=(SELECT MAX(ts) FROM captures c2 WHERE c2.canon=c.canon "
        "AND c2.hidden=0 AND c2.junk=0) GROUP BY c.canon ORDER BY lower(c.name)", sorted(DOC_EXTS)).fetchall()
    groups = {k: [] for k in kinds}
    for r in rows:
        groups[r["kind"] if r["kind"] in groups else "doc"].append(r)
    member = cdb().execute(
        "SELECT a.*, p.title, p.board, u.name AS uname FROM attachments a JOIN posts p ON p.id=a.post_id "
        "JOIN users u ON u.id=a.user_id WHERE p.hidden=0 ORDER BY a.id DESC LIMIT 200").fetchall()
    return render_template("files.html", active="files", kinds=kinds, groups=groups, member=member)


def career_data():
    path = os.path.join(_CFG["base"], "career.json")
    mtime = os.path.getmtime(path)
    if _CAREER["mtime"] != mtime:
        with open(path, encoding="utf-8") as f:
            _CAREER.update(mtime=mtime, data=json.load(f))
    return _CAREER["data"]


@bp.route("/person")
def person():
    return render_template("person.html", active="person", c=career_data())
