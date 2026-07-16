#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    package = Path(sys.argv[1] if len(sys.argv) > 1 else 'wechat-package').resolve()
    manifest_path = package / 'manifest.json'
    data = json.loads(manifest_path.read_text(encoding='utf-8'))
    required = ['schema_version','publish_key','profile_id','report_date','title','digest','content_file','cover']
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise SystemExit(f'missing manifest fields: {missing}')
    if data['schema_version'] != 2:
        raise SystemExit('schema_version must be 2')
    content = (package / data['content_file']).resolve()
    if package not in content.parents or not content.is_file():
        raise SystemExit('invalid content_file')
    cover = data['cover']
    cover_file = (package / cover['file']).resolve()
    if package not in cover_file.parents or not cover_file.is_file():
        raise SystemExit('invalid cover file')
    actual = sha256(cover_file)
    if cover.get('sha256') != actual:
        raise SystemExit(f'cover sha mismatch: {actual}')
    print(json.dumps({'status':'ok','publish_key':data['publish_key'],'cover_sha256':actual}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
