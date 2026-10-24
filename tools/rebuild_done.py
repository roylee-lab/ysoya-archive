#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
지워진 done.tsv 다시 만들기

ysoya_recover.py 는 받은 폴더에 inventory.tsv(각 캡처를 어느 파일로 저장했는지)와
cdx_cache.json(아카이브가 기록한 해시)도 남긴다. 이 둘과 실제 파일을 대조해 done.tsv 를 다시 만든다.
다시 받지 않으므로 인터넷이 필요 없다.

  py tools\\rebuild_done.py ..\\ysoya_archive

이미 done.tsv 가 있으면 done.tsv.bak 으로 옮겨 두고 새로 만든다.
"""
import base64
import csv
import hashlib
import json
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

FIELDS = ["original", "timestamp", "path", "size", "sha1", "verified", "note"]


def sha1_b32(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return base64.b32encode(h.digest()).decode()


def main():
    if len(sys.argv) != 2:
        sys.exit("사용법: py tools\\rebuild_done.py ..\\ysoya_archive")
    root = sys.argv[1]
    inv_path = os.path.join(root, "inventory.tsv")
    if not os.path.isfile(inv_path):
        sys.exit(f"{inv_path} 도 없습니다. 이 경우에는 ysoya_recover.py 를 다시 실행해야 합니다.")

    digests = {}
    cache = os.path.join(root, "cdx_cache.json")
    if os.path.isfile(cache):
        with open(cache, encoding="utf-8") as f:
            for r in json.load(f).get("rows", []):
                if r.get("digest"):
                    digests[(r.get("original"), r.get("timestamp"))] = r["digest"]
    else:
        print("  (cdx_cache.json 이 없어 해시 대조는 건너뜀. 사이트는 그래도 동작함)")

    with open(inv_path, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f, delimiter="\t") if r.get("saved_as")]
    print(f"inventory.tsv: 저장 경로가 있는 캡처 {len(rows):,}개")

    out, missing, verified = [], 0, 0
    seen = set()
    for i, r in enumerate(rows, 1):
        key = (r["original"], r["timestamp"])
        if key in seen:
            continue
        seen.add(key)
        rel = r["saved_as"]
        full = os.path.join(root, rel.replace("\\", os.sep).replace("/", os.sep))
        if not os.path.isfile(full):
            missing += 1                              # 받지 못했거나(404 등) 백신이 지운 파일
            continue
        h = sha1_b32(full)
        ok = digests.get(key) == h
        verified += ok
        out.append({"original": r["original"], "timestamp": r["timestamp"], "path": rel,
                    "size": os.path.getsize(full), "sha1": h, "verified": "yes" if ok else "no",
                    "note": "" if ok or key not in digests else "다른 시점 내용이거나 받은 뒤 고친 파일"})
        if i % 2000 == 0:
            print(f"  {i:,}/{len(rows):,}")

    target = os.path.join(root, "done.tsv")
    if os.path.exists(target):
        os.replace(target, target + ".bak")
        print("  기존 done.tsv 는 done.tsv.bak 으로 옮겼습니다")
    with open(target, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, delimiter="\t", lineterminator="\r\n")
        w.writeheader()
        w.writerows(out)
    print(f"\n완료: {target}")
    print(f"  기록 {len(out):,}개 (해시 일치 {verified:,}개), 폴더에 없는 파일 {missing:,}개는 뺐습니다")


if __name__ == "__main__":
    main()
