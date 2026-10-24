# -*- coding: utf-8 -*-
"""
YSOYA_Archive 공통 로직 (색인 만들기와 웹 서버가 같이 씀)

- canon()        : 같은 주소의 여러 표기(www 유무, :80, ~ 와 %7E)를 하나로 묶는 '대표 주소'
- sniff()        : 파일 앞부분으로 실제 형식 판별
- decode_page()  : 옛 한국어·일본어 페이지를 글자 깨짐 없이 읽기
- rewrite_html() : 보존된 페이지 안의 링크를 이 사이트 주소로 바꾸기
- find_capture() : 요청한 주소·시점에 가장 가까운 캡처 찾기
"""
import calendar
import html as htmllib
import json
import os
import re
from urllib.parse import quote, urljoin, urlsplit

PAGE_EXT = {".html", ".htm", ".shtml", ".php", ".php3", ".phtml", ".cgi", ".asp", ".jsp", ""}
KIND_BY_EXT = {
    "image": {".jpg", ".jpeg", ".gif", ".png", ".bmp", ".ico", ".tif", ".tiff"},
    "video": {".avi", ".mpg", ".mpeg", ".asf", ".wmv", ".mov", ".rm", ".rmvb", ".swf", ".flv", ".mp4", ".webm"},
    "audio": {".mp3", ".mid", ".midi", ".wav", ".wma", ".ogg", ".ogx", ".ra", ".vqf", ".s3m", ".xm", ".mod", ".it"},
    "playlist": {".asx", ".wvx", ".wax", ".ram", ".rpm", ".smil", ".smi", ".m3u", ".pls"},
    "archive": {".zip", ".lzh", ".lha", ".rar", ".alz", ".7z", ".gz", ".tgz", ".cab"},
    "program": {".exe", ".com", ".msi", ".bin", ".iso", ".dll", ".scr", ".pif"},
    "style": {".css", ".js", ".txt", ".xml", ".ini"},
}
# 확장자에서 기대하는 실제 형식 (sniff 결과와 비교해 가짜 파일을 걸러냄)
EXPECT = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".gif": "gif", ".bmp": "bmp", ".zip": "zip",
          ".mid": "midi", ".midi": "midi", ".wav": "riff", ".avi": "riff", ".mp3": "mp3", ".swf": "swf",
          ".rm": "realmedia", ".asf": "asf", ".wmv": "asf", ".wma": "asf", ".mpg": "mpeg", ".mpeg": "mpeg",
          ".exe": "exe", ".pdf": "pdf", ".ogg": "ogg", ".ogx": "ogg"}
SIG = [
    (b"\xff\xd8\xff", "jpeg"), (b"\x89PNG\r\n\x1a\n", "png"), (b"GIF87a", "gif"), (b"GIF89a", "gif"),
    (b"BM", "bmp"), (b"PK\x03\x04", "zip"), (b"PK\x05\x06", "zip"), (b"Rar!", "rar"), (b"MZ", "exe"),
    (b"MThd", "midi"), (b"RIFF", "riff"), (b"ID3", "mp3"), (b"\xff\xfb", "mp3"), (b"\xff\xfa", "mp3"),
    (b"\xff\xf3", "mp3"), (b"\xff\xf2", "mp3"), (b"\xff\xe3", "mp3"), (b"FWS", "swf"), (b"CWS", "swf"),
    (b".RMF", "realmedia"), (b"\x30\x26\xb2\x75", "asf"), (b"\x00\x00\x01\xba", "mpeg"),
    (b"\x00\x00\x01\xb3", "mpeg"), (b"%PDF", "pdf"), (b"OggS", "ogg"), (b"TWIN", "vqf"),
]
MIME = {".html": "text/html", ".htm": "text/html", ".shtml": "text/html", ".php": "text/html",
        ".cgi": "text/html", ".asp": "text/html", ".jsp": "text/html", ".phtml": "text/html",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
        ".bmp": "image/bmp", ".ico": "image/x-icon", ".css": "text/css", ".js": "text/javascript",
        ".txt": "text/plain", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
        ".ogx": "audio/ogg", ".mid": "audio/midi", ".midi": "audio/midi", ".mp4": "video/mp4",
        ".webm": "video/webm", ".mpg": "video/mpeg", ".mpeg": "video/mpeg", ".wmv": "video/x-ms-wmv",
        ".asf": "video/x-ms-asf", ".avi": "video/x-msvideo", ".rm": "application/vnd.rn-realmedia",
        ".swf": "application/x-shockwave-flash", ".zip": "application/zip", ".pdf": "application/pdf"}
BROWSER_PLAYABLE = {".mp3", ".wav", ".ogg", ".ogx", ".mp4", ".webm", ".m4a"}
CHARSET_RE = re.compile(rb'charset\s*=\s*["\']?([A-Za-z0-9_\-]+)', re.I)
CHARSET_ALIAS = {"euc-kr": "cp949", "euckr": "cp949", "ks_c_5601-1987": "cp949", "ksc5601": "cp949",
                 "x-windows-949": "cp949", "shift_jis": "cp932", "shift-jis": "cp932", "sjis": "cp932",
                 "x-sjis": "cp932", "euc-jp": "euc_jp", "iso-2022-jp": "iso2022_jp"}


# ------------------------------------------------------------------ 설정
def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(path):
    """site_config.json 을 읽고, 같은 폴더에 site_config.local.json 이 있으면 그 값으로 덮어쓴다.
    (서버마다 다른 설정은 .local.json 에 두면 git pull 과 부딪히지 않음)"""
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    local = os.path.splitext(path)[0] + ".local.json"
    if os.path.isfile(local):
        with open(local, encoding="utf-8") as f:
            _merge(cfg, json.load(f))
    cfg["_hidden"] = [p.lower() for p in cfg.get("hidden_prefixes", [])]
    cfg["_hidden_exts"] = {e.lower() for e in cfg.get("hidden_exts", [])}
    cfg["_sections"] = sorted(((k.lower(), v) for k, v in cfg.get("sections", {}).items()),
                              key=lambda kv: -len(kv[0]))
    cfg["_diary"] = re.compile(cfg.get("diary_pattern", r"$^"), re.I)
    return cfg


# ------------------------------------------------------------------ 주소
def canon(url):
    """'http://www.ysoya.net:80/%7Eysoya/a.html' → 'ysoya.net/~ysoya/a.html'"""
    url = url.strip()
    if "://" not in url[:12]:
        url = "http://" + url
    sp = urlsplit(url)
    host = (sp.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"%7[eE]", "~", sp.path or "/")
    path = re.sub(r"/{2,}", "/", path)
    if not path.startswith("/"):
        path = "/" + path
    return host + path + ("?" + sp.query if sp.query else "")


def split_canon(c):
    host, _, rest = c.partition("/")
    path, _, query = ("/" + rest).partition("?")
    return host, path, query


def ext_of(c):
    _, path, _ = split_canon(c)
    return os.path.splitext(path.rsplit("/", 1)[-1])[1].lower()


def kind_of(ext, sniffed):
    if ext in PAGE_EXT:
        return "page"
    return _kind_from_ext(ext)


def _kind_from_ext(ext):
    for k, exts in KIND_BY_EXT.items():
        if ext in exts:
            return k
    return "other"


def section_of(c, cfg):
    lc = c.lower()
    for prefix, label in cfg["_sections"]:
        if lc.startswith(prefix):
            return label
    host, path, _ = split_canon(c)
    first = path.strip("/").split("/")[0]
    return host + ("/" + first if first and "." not in first else "")


def is_hidden(c, cfg):
    lc = c.lower()
    if any(lc.startswith(p) for p in cfg["_hidden"]):
        return True
    return ext_of(c) in cfg["_hidden_exts"]


# ------------------------------------------------------------------ 시각
def ts_pad(ts):
    ts = re.sub(r"\D", "", ts or "")[:14]
    if not ts:
        return "20000101000000"
    y = ts[:4].ljust(4, "0")
    mo = ts[4:6] if len(ts) >= 6 else "01"
    d = ts[6:8] if len(ts) >= 8 else "01"
    rest = ts[8:14].ljust(6, "0")
    mo = "01" if mo in ("00", "") else mo
    d = "01" if d in ("00", "") else d
    return y + mo + d + rest


def ts_epoch(ts):
    t = ts_pad(ts)
    try:
        return calendar.timegm((int(t[:4]), min(max(int(t[4:6]), 1), 12), min(max(int(t[6:8]), 1), 31),
                                int(t[8:10]) % 24, int(t[10:12]) % 60, int(t[12:14]) % 60))
    except (ValueError, OverflowError):
        return 0


def ts_label(ts):
    t = ts_pad(ts)
    return f"{t[:4]}-{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}"


# ------------------------------------------------------------------ 형식 판별·글자
def sniff(head):
    for magic, kind in SIG:
        if head.startswith(magic):
            return kind
    if head[2:5] in (b"-lh", b"-lz"):
        return "lzh"
    low = head[:4096].lower()
    if any(t in low for t in (b"<html", b"<!doctype", b"<head", b"<body", b"<script", b"<meta", b"<table", b"<frameset")):
        return "html"
    return "unknown"


def page_codec(data):
    m = CHARSET_RE.search(data[:4096])
    enc = m.group(1).decode("ascii", "ignore").lower() if m else ""
    if enc in ("utf-8", "utf8"):
        return "utf-8"
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    codec = CHARSET_ALIAS.get(enc, enc) or "cp949"
    try:
        "".encode(codec)
        return codec
    except LookupError:
        return "cp949"


def decode_page(data):
    codec = page_codec(data)
    return data.decode(codec, errors="replace"), codec


TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
HEAD_RE = re.compile(r"<h[1-3][^>]*>(.*?)</h[1-3]>", re.I | re.S)


QUOTED_TAG_RE = re.compile(r"""<[a-zA-Z/!?][^<>"']*(?:(?:"[^"]*"|'[^']*')[^<>"']*)*>""")


def strip_tags(s):
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", s)
    s = re.sub(r"(?s)<!--.*?-->", " ", s)
    s = QUOTED_TAG_RE.sub(" ", s)          # 따옴표 안에 > 가 들어 있는 태그까지 통째로
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def page_title(text):
    for rx in (TITLE_RE, HEAD_RE):
        m = rx.search(text)
        if m:
            t = strip_tags(m.group(1))
            if t:
                return t[:200]
    return ""


# ------------------------------------------------------------------ 이 사이트 주소 만들기
def enc_target(c):
    """대표 주소를 경로 안에 안전하게 넣기 ( % ? # 공백 등을 한 번 더 감쌈 )"""
    return quote(c, safe="/:~@!$&'()*+,;=-._")


def view_href(ts, c):
    return f"/view/{ts}/{enc_target(c)}"


def raw_href(ts, c):
    return f"/raw/{ts}/{enc_target(c)}"


def normalize_link(abs_url, codec):
    """페이지에서 읽은 링크(한글 그대로일 수 있음)를 원래 서버가 쓰던 인코딩의 %XX 형태로"""
    sp = urlsplit(abs_url)
    keep = "/%~:@!$&'()*+,;=-._"
    try:
        path = quote(sp.path or "/", safe=keep, encoding=codec, errors="strict")
        query = quote(sp.query, safe=keep + "?", encoding=codec, errors="strict")
    except (UnicodeEncodeError, LookupError):
        path = quote(sp.path or "/", safe=keep)
        query = quote(sp.query, safe=keep + "?")
    netloc = sp.netloc
    return f"{sp.scheme}://{netloc}{path}" + (f"?{query}" if query else "")


# ------------------------------------------------------------------ HTML 다시 쓰기
# 따옴표 안의 < > 까지 고려한 태그 (옛 페이지는 툴팁 문자열 안에 <font> 같은 태그를 넣어 둠)
TAG_RE = re.compile(r"""<([a-zA-Z][a-zA-Z0-9]*)((?:\s+[^\s"'<>/=]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'>]+))?)*)\s*(/?)>""", re.S)
ATTR_RE = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s"'>]+))?""", re.S)
CSS_URL_RE = re.compile(r"""url\(\s*(["']?)([^"')]+)\1\s*\)""", re.I)
NAV_TAGS = {"a", "area"}
FRAME_TAGS = {"frame", "iframe"}
URL_ATTRS = {"href", "src", "background", "action", "lowsrc", "dynsrc", "poster", "data"}


def rewrite_html(text, page_url, ts, codec):
    """보존된 페이지의 모든 링크를 /view·/raw 주소로 바꾼다. (text: 이미 디코딩된 문자열)"""
    base = page_url if "://" in page_url[:12] else "http://" + page_url
    m = re.search(r"""<base\s[^>]*href\s*=\s*["']?([^"'\s>]+)""", text, re.I)
    if m:
        base = urljoin(base, htmllib.unescape(m.group(1)))
        text = re.sub(r"<base\s[^>]*>", "", text, flags=re.I)

    def target(raw):
        raw = htmllib.unescape(raw).strip()
        if not raw or raw.startswith("#") or re.match(r"(?i)(javascript|mailto|data|about|tel):", raw):
            return None
        raw = re.sub(r"(?i)^(mms|rtsp|pnm)://", "http://", raw)
        try:
            absu = urljoin(base, raw.replace("\\", "/"))
        except ValueError:
            return None
        if not absu.lower().startswith(("http://", "https://")):
            return None
        return canon(normalize_link(absu.split("#", 1)[0], codec))

    def css_fix(css):
        def rep(mm):
            c = target(mm.group(2))
            return f'url("{raw_href(ts, c)}")' if c else mm.group(0)
        return CSS_URL_RE.sub(rep, css)

    def fix_tag(mt):
        tag = mt.group(1).lower()
        attrs_s = mt.group(2) or ""
        if not attrs_s:
            return mt.group(0)
        attrs = [(a.group(1), a.group(2)) for a in ATTR_RE.finditer(attrs_s)]
        names = {k.lower(): v for k, v in attrs}
        tgt_attr = (names.get("target") or "").strip("\"'").lower()
        out, changed, had_target, nav_rewritten = [], False, False, False
        for k, v in attrs:
            kl = k.lower()
            if v is None:
                out.append(k)
                continue
            val = v[1:-1] if v[:1] in "\"'" else v
            new = None
            if kl in URL_ATTRS or (tag == "param" and kl == "value" and (names.get("name") or "").strip("\"'").lower() in ("movie", "src", "filename", "url")):
                c = target(val)
                if c:
                    if tag in NAV_TAGS and kl == "href" and tgt_attr in ("", "_top", "_self", "_parent", "_blank"):
                        new = view_href(ts, c)
                        nav_rewritten = True
                    else:
                        new = raw_href(ts, c)
            elif kl == "style":
                fixed = css_fix(htmllib.unescape(val))
                if fixed != htmllib.unescape(val):
                    new = fixed
            elif kl == "content" and tag == "meta" and re.search(r"url\s*=", val, re.I):
                mm = re.search(r"(?i)(url\s*=\s*)(.+)$", val)
                c = target(mm.group(2).strip("'\" "))
                if c:
                    new = val[:mm.start(2)] + raw_href(ts, c)
            elif kl == "target":
                had_target = True
                if tag in NAV_TAGS and val.lower() in ("_self", "_parent", "") :
                    new = "_top"
            if kl.startswith("on"):
                changed = True          # 스크립트 이벤트는 실행되지 않으므로 지움
                continue
            if new is not None:
                changed = True
                out.append(f'{k}="{htmllib.escape(new, quote=True)}"')
            else:
                out.append(f"{k}={v}")
        if nav_rewritten and not had_target:
            out.append('target="_top"')
            changed = True
        if not changed:
            return mt.group(0)
        return f"<{mt.group(1)} {' '.join(out)}{mt.group(3)}>"

    text = TAG_RE.sub(fix_tag, text)
    text = re.sub(r"(?is)(<style\b[^>]*>)(.*?)(</style>)", lambda m: m.group(1) + css_fix(m.group(2)) + m.group(3), text)
    # 원래 charset 선언은 지우고 UTF-8 로 선언
    text = re.sub(r"(?i)<meta[^>]+charset[^>]*>", "", text)
    head = '<meta charset="utf-8">'
    if re.search(r"(?i)<head[^>]*>", text):
        text = re.sub(r"(?i)(<head[^>]*>)", lambda m: m.group(1) + head, text, count=1)
    else:
        text = head + text
    return text


# ------------------------------------------------------------------ 캡처 찾기
INDEX_NAMES = ("index.html", "index.htm", "index.shtml", "index.php", "index.phtml", "index.cgi", "index_e.shtml")


def candidate_keys(c, cfg):
    """(대표 주소, 단계) 목록. 단계 0=그대로, 1=색인 파일·슬래시 보정, 2=같은 사이트의 다른 위치"""
    host, path, query = split_canon(c)
    q = "?" + query if query else ""
    keys = [(c, 0)]
    if path.endswith("/"):
        keys += [(host + path + n + q, 1) for n in INDEX_NAMES]
    else:
        last = path.rsplit("/", 1)[-1]
        if "." not in last:
            keys.append((host + path + "/" + q, 1))
            keys += [(host + path + "/" + n + q, 1) for n in INDEX_NAMES]
        elif last.lower().startswith("index."):
            keys.append((host + path.rsplit("/", 1)[0] + "/" + q, 1))
    for group in cfg.get("mirrors", []):
        for k, _ in list(keys):
            for p in group:
                if k.lower().startswith(p.lower()):
                    rest = k[len(p):]
                    for other in group:
                        if other != p:
                            keys.append((other + rest, 2))
                    break
    seen, out = set(), []
    for k, tier in keys:
        if k not in seen:
            seen.add(k)
            out.append((k, tier))
    return out


def find_capture(db, c, ts, cfg, include_hidden=False):
    """가장 알맞은 캡처 한 개와 그 단계를 돌려준다. 없으면 (None, None, 숨김여부)."""
    keys = candidate_keys(c, cfg)
    tiers = {k: t for k, t in keys}
    tiers_lc = {}
    for k, t in keys:
        tiers_lc.setdefault(k.lower(), t)
    q = ",".join("?" * len(keys))
    rows = db.execute(f"SELECT * FROM captures WHERE canon IN ({q}) AND junk=0", [k for k, _ in keys]).fetchall()
    if not rows:
        rows = db.execute(f"SELECT * FROM captures WHERE canon_lc IN ({q}) AND junk=0", [k.lower() for k, _ in keys]).fetchall()
    hidden_hit = any(r["hidden"] for r in rows)
    if not include_hidden:
        rows = [r for r in rows if not r["hidden"]]
    if not rows:
        return None, None, hidden_hit
    want = ts_epoch(ts)

    def score(r):
        t = tiers.get(r["canon"], tiers_lc.get(r["canon_lc"], 3))
        return (t, abs(ts_epoch(r["ts"]) - want))
    best = min(rows, key=score)
    return best, score(best)[0], hidden_hit
