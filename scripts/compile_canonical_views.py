#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
from pifactory.canonical_compiler import compile_profile_views

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--project-root',default='.'); ap.add_argument('--profile',default='all'); args=ap.parse_args()
 root=Path(args.project_root).resolve(); vr=root/'config/vocabularies'
 dirs=[vr/args.profile] if args.profile!='all' else sorted(x for x in vr.iterdir() if x.is_dir())
 rows=[]
 for d in dirs: rows.append(compile_profile_views(d))
 print(json.dumps({'compiled':len(rows),'profiles':[x['profile_id'] for x in rows]},ensure_ascii=False))
 return 0
if __name__=='__main__': raise SystemExit(main())
