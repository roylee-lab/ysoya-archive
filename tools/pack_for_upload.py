#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
서버에 올릴 자료 묶기 (PythonAnywhere 처럼 업로드 크기·저장 공간이 작은 곳용)

내 컴퓨터에서 만든 색인(data/archive.db)을 보고, 사이트에 실제로 보이는 파일만 골라
업로드하기 좋은 크기의 zip 여러 개로 나눈다. 숨김 구역(방명록 등)과 쓸모없는 캡처는 넣지 않으므로
다른 사람의 글이 내 컴퓨터 밖으로 나가지 않는다.

  python tools/pack_for_upload.py --roots ../ysoya_archive
  python tools/pack_for_upload.py --roots ../ysoya_archive --max-mb 90 --out upload

만들어지는 것
  upload/ysoya_archive_part1.zip, part2.zip ...   (각각 압축을 풀면 ysoya_archive/ 폴더가 생김)
  각 zip 은 따로따로 풀어도 되고, done.tsv 는 part1 에 들어 있음
"""
import argparse
import csv
import io
import os
import sqlite3
import sys
import zipfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)


def main():
    ap = argparse.ArgumentParser(description="보이는 파일만 골라 업로드용 zip 만들기")
    ap.add_argument("--roots", action="append", required=True, help="받은 폴더 (여러 번 지정 가능)")
    ap.add_argument("--data", default=os.path.join(PROJECT, "data"), help="archive.db 가 있는 폴더")
    ap.add_argument("--out", default=os.path.join(PROJECT, "upload"), help="zip 을 만들 폴더")
    ap.add_argument("--max-mb", type=float, default=90, help="zip 하나의 최대 크기 (PythonAnywhere 업로드 한도는 100MB)")
    ap.add_argument("--include-hidden", action="store_true", help="숨김 구역 파일도 넣기 (권장하지 않음)")
    a = ap.parse_args()

    db_path = os.path.join(a.data, "archive.db")
    if not os.path.isfile(db_path):
        sys.exit(f"{db_path} 가 없습니다. 먼저 python build_index.py --roots ... 로 색인을 만드세요.")
    roots = {os.path.basename(os.path.normpath(r)): os.path.abspath(r) for r in a.roots}
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    where = "junk=0" if a.include_hidden else "junk=0 AND hidden=0"
    rows = db.execute(f"SELECT root, file, size FROM captures WHERE {where} ORDER BY root, file").fetchall()
    missing_roots = sorted({r[0] for r in rows} - set(roots))
    if missing_roots:
        sys.exit(f"색인에 있는 폴더를 --roots 로 알려 주세요: {', '.join(missing_roots)}")

    os.makedirs(a.out, exist_ok=True)
    limit = int(a.max_mb * 1048576)
    total_files, total_bytes = 0, 0
    for root_name, root_path in roots.items():
        files = [(f, s) for r, f, s in rows if r == root_name]
        if not files:
            continue
        keep = {f.replace("\\", "/") for f, _ in files}
        # done.tsv 에서 넣을 파일의 줄만 남김
        done_buf = io.StringIO()
        with open(os.path.join(root_path, "done.tsv"), encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            writer = csv.DictWriter(done_buf, fieldnames=reader.fieldnames, delimiter="\t", lineterminator="\n")
            writer.writeheader()
            kept_rows = 0
            for row in reader:
                if row.get("path", "").replace("\\", "/").lstrip("/") in keep:
                    row["path"] = row["path"].replace("\\", "/")
                    writer.writerow(row)
                    kept_rows += 1

        part, used, zf = 0, 0, None

        def new_part():
            nonlocal part, used, zf
            if zf:
                zf.close()
            part += 1
            used = 0
            name = os.path.join(a.out, f"{root_name}_part{part}.zip")
            zf = zipfile.ZipFile(name, "w", zipfile.ZIP_DEFLATED, compresslevel=6)
            print(f"  만드는 중: {name}")
            return zf

        new_part()
        zf.writestr(f"{root_name}/done.tsv", done_buf.getvalue().encode("utf-8"))
        for rel, size in files:
            src = os.path.join(root_path, rel)
            if not os.path.isfile(src):
                continue
            if used and used + size > limit * 0.97:      # 압축 전 크기로 넉넉하게 나눔
                new_part()
            zf.write(src, f"{root_name}/{rel.replace(chr(92), '/')}")
            used += size
            total_files += 1
            total_bytes += size
        zf.close()
        print(f"[{root_name}] 파일 {len(files):,}개, done.tsv {kept_rows:,}줄 → zip {part}개")

    print(f"\n완료: 파일 {total_files:,}개, {total_bytes / 1048576:.1f}MB (압축 전)")
    for name in sorted(os.listdir(a.out)):
        if name.endswith(".zip"):
            mb = os.path.getsize(os.path.join(a.out, name)) / 1048576
            flag = "  ← 100MB 를 넘어 업로드할 수 없음. --max-mb 를 줄이세요" if mb >= 100 else ""
            print(f"  {name}  {mb:.1f}MB{flag}")


if __name__ == "__main__":
    main()
