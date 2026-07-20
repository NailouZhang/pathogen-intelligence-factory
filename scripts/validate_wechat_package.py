#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from html.parser import HTMLParser
from pathlib import Path


class VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text:
            self.parts.append(text)


def visible_text_count(text: str) -> int:
    parser = VisibleTextParser()
    parser.feed(text)
    return len("".join(parser.parts))


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
    if data.get('contract') != 'pathogen-wechat-package/v2':
        raise SystemExit('contract must be pathogen-wechat-package/v2')
    content = (package / data['content_file']).resolve()
    if package not in content.parents or not content.is_file():
        raise SystemExit('invalid content_file')
    content_text = content.read_text(encoding='utf-8')
    visible = visible_text_count(content_text)
    budget_path = package / 'content-budget-audit.json'
    if budget_path.is_file():
        budget = json.loads(budget_path.read_text(encoding='utf-8'))
        maximum = int(budget.get('max_visible_chars') or 48000)
        if visible > maximum or budget.get('within_budget') is not True:
            raise SystemExit(f'wechat visible text budget exceeded: {visible}>{maximum}')
        if int(budget.get('visible_chars_after') or -1) != visible:
            raise SystemExit('wechat content budget audit does not match article.html')
        if int(budget.get('minimum_full_papers') or 0) < 10:
            raise SystemExit('wechat minimum full papers must be at least 10')
        if budget.get('policy_version') == 'v16.1-wechat-visible-text-budget-2':
            for prefix in ('primary_papers', 'supplementary_papers', 'supplementary_news', 'main_news'):
                total = int(budget.get(f'{prefix}_total') or 0)
                displayed = int(budget.get(f'{prefix}_displayed') or 0)
                omitted = int(budget.get(f'{prefix}_omitted') or 0)
                if min(total, displayed, omitted) < 0 or displayed + omitted != total:
                    raise SystemExit(f'invalid WeChat display accounting for {prefix}')
            omitted_total = sum(int(budget.get(key) or 0) for key in (
                'primary_papers_omitted', 'supplementary_papers_omitted', 'supplementary_news_omitted', 'main_news_omitted'
            ))
            if omitted_total and '微信公众号篇幅说明' not in content_text:
                raise SystemExit('WeChat omission notice missing from article.html')
            if budget.get('full_catalog_preserved_in_source_data') is not True:
                raise SystemExit('source catalog preservation flag must be true')
    elif visible > 48000:
        raise SystemExit(f'wechat visible text budget exceeded without audit: {visible}>48000')
    cover = data['cover']
    cover_file = (package / cover['file']).resolve()
    if package not in cover_file.parents or not cover_file.is_file():
        raise SystemExit('invalid cover file')
    actual = sha256(cover_file)
    if cover.get('sha256') != actual:
        raise SystemExit(f'cover sha mismatch: {actual}')
    print(json.dumps({'status':'ok','publish_key':data['publish_key'],'cover_sha256':actual,'visible_chars':visible}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
