#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--schedule",
        type=Path,
        default=ROOT / "config" / "weekly_virus_schedule.yaml",
    )
    args = parser.parse_args()

    data_root = args.data_root.resolve()
    output = args.output.resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    schedule = yaml.safe_load(args.schedule.read_text(encoding="utf-8")) or {}
    ordered = [
        profile
        for day in (
            "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
        )
        for profile in (schedule.get("week") or {}).get(day, [])
    ]
    day_by_profile = {
        profile: day
        for day, profiles in (schedule.get("week") or {}).items()
        for profile in profiles
    }

    cards = []
    profiles_root = data_root / "profiles"
    for profile_id in ordered:
        base = profiles_root / profile_id
        site = base / "site"
        issue = read_json(base / "data" / "latest.json")
        if not site.is_dir() or not (site / "index.html").is_file():
            continue
        target = output / "profiles" / profile_id
        shutil.copytree(site, target, dirs_exist_ok=True)
        profile = issue.get("profile") or {}
        metrics = issue.get("metrics") or {}
        cover = f"profiles/{profile_id}/assets/cover.jpg"
        cards.append({
            "profile_id": profile_id,
            "name_zh": profile.get("display_name_zh") or profile_id,
            "name_en": profile.get("display_name_en") or profile_id,
            "issue_date": issue.get("issue_date") or "尚未生成",
            "papers": metrics.get("papers", 0),
            "news": metrics.get("news", 0),
            "day": day_by_profile.get(profile_id, ""),
            "cover": cover,
        })

    card_html = "\n".join(
        f'''<a class="card" href="profiles/{html.escape(row['profile_id'])}/">
<img src="{html.escape(row['cover'])}" alt="{html.escape(row['name_zh'])}">
<div class="body"><span class="day">{html.escape(row['day'].title())}</span>
<h2>{html.escape(str(row['name_zh']))}</h2>
<p class="en">{html.escape(str(row['name_en']))}</p>
<p>最新报告：{html.escape(str(row['issue_date']))}</p>
<div class="metrics"><span>文献 {row['papers']}</span><span>新闻 {row['news']}</span></div></div></a>'''
        for row in cards
    ) or '<p class="empty">尚无已生成报告。首次工作流完成后会自动显示。</p>'

    index = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>全球病毒文献情报周循环</title>
<style>
:root{{--navy:#2c3e50;--green:#27ae60;--red:#c53030;--cream:#fffcf0;--bg:#f4f7f9;--muted:#718096}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);font-family:Arial,"Noto Sans CJK SC","Microsoft YaHei",sans-serif;color:#263442}}
.hero{{background:linear-gradient(135deg,#263b50,#344e65);color:white;padding:42px 22px;text-align:center}}
.hero h1{{margin:0;font-size:30px}}.hero p{{opacity:.86;line-height:1.8}}
.wrap{{max-width:1180px;margin:26px auto;padding:0 18px}}.notice{{background:var(--cream);border-left:6px solid #fbd38d;padding:18px 20px;border-radius:10px;margin-bottom:22px;line-height:1.8}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px}}
.card{{display:block;background:white;border-radius:14px;overflow:hidden;text-decoration:none;color:inherit;box-shadow:0 5px 20px rgba(25,55,80,.09);transition:.2s}}
.card:hover{{transform:translateY(-3px);box-shadow:0 9px 28px rgba(25,55,80,.15)}}.card img{{width:100%;height:150px;object-fit:cover;background:#2c3e50}}
.body{{padding:17px 18px 19px}}.day{{font-size:12px;color:white;background:var(--green);padding:4px 9px;border-radius:999px}}
h2{{margin:13px 0 5px;color:#1a365d;font-size:21px}}.en{{color:var(--muted);min-height:38px}}.metrics{{display:flex;gap:10px;margin-top:13px}}
.metrics span{{background:#f0fff4;color:#1e7e34;border-radius:8px;padding:7px 10px;font-size:13px}}footer{{text-align:center;color:#718096;padding:30px}}.empty{{padding:30px;background:white;border-radius:12px}}
</style></head><body>
<section class="hero"><h1>全球病毒文献情报周循环</h1><p>北京时间每日 02:00 顺序运行 · 每种病毒每周一次 · 过去 7 天高质量文献与权威新闻</p></section>
<main class="wrap"><div class="notice"><strong>调度策略：</strong>周一 3 种病毒，其余每天 2 种；每个病原最多展示质量排序后的前 50 篇文献和前 50 条权威新闻。点击卡片进入该病原的完整双语日报。</div>
<div class="grid">{card_html}</div></main><footer>Pathogen Intelligence Factory · GitHub Actions + Evidence-aware LLM pipeline</footer></body></html>'''
    (output / "index.html").write_text(index, encoding="utf-8")
    (output / "portal.json").write_text(json.dumps(cards, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print({"profiles": len(cards), "output": str(output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
