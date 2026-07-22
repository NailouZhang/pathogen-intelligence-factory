#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

EXPECTED={
 'canonical_vocabulary.json':['src/pifactory/bundled_vocabulary.py'],
 'profile.json':['src/pifactory/bundled_vocabulary.py'],
 'retrieval_vocabulary.json':['src/pifactory/bundled_vocabulary.py'],
 'review_vocabulary.json':['src/pifactory/bundled_vocabulary.py'],
 'exclusion_vocabulary.json':['src/pifactory/bundled_vocabulary.py'],
 'translation_glossary.json':['src/pifactory/bundled_vocabulary.py'],
 'authoritative_sources.json':['src/pifactory/bundled_vocabulary.py'],
 'validation_cases.json':['src/pifactory/bundled_vocabulary.py','scripts/validate_canonical_vocabularies.py'],
}
PROMPTS={
 'relevance_review.md':['src/pifactory/relevance.py'],
 'research_analysis.md':['src/pifactory/analysis.py'],
 'review_analysis.md':['src/pifactory/analysis.py'],
 'news_analysis.md':['src/pifactory/analysis.py'],
 'field_repair.md':['src/pifactory/analysis.py'],
 'translate_zh.md':['src/pifactory/translation.py'],
 'ambiguous_dedup.md':['src/pifactory/pipeline_v15.py'],
 'profile_bootstrap_v3.md':['src/pifactory/bootstrap.py'],
 'review_vocabulary_v1.md':['scripts/refresh_canonical_vocabulary.py'],
}

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--project-root',default='.'); ap.add_argument('--output',default='VOCABULARY_CONSUMER_AUDIT.json'); args=ap.parse_args()
 root=Path(args.project_root).resolve(); errors=[]; file_rows=[]
 profiles=sorted(d for d in (root/'config/vocabularies').iterdir() if d.is_dir())
 for name,consumers in EXPECTED.items():
  missing=[d.name for d in profiles if not (d/name).is_file()]
  unreferenced=[c for c in consumers if name not in (root/c).read_text(encoding='utf-8')]
  if missing: errors.append(f'{name} missing in {missing}')
  if unreferenced: errors.append(f'{name} not referenced by {unreferenced}')
  derivation_failures=[]; counts=[]; hashes={}
  for d in profiles:
   if not (d/name).is_file(): continue
   value=json.loads((d/name).read_text(encoding='utf-8'))
   canonical=json.loads((d/'canonical_vocabulary.json').read_text(encoding='utf-8'))
   fingerprint=canonical.get('semantic_fingerprint')
   if name!='canonical_vocabulary.json' and value.get('derived_from_semantic_fingerprint')!=fingerprint:
    derivation_failures.append(d.name)
   hashes[d.name]=sha(d/name)
   if name=='canonical_vocabulary.json':
    topic=value.get('topic_contract') or {}
    counts.append(sum(len(topic.get(key) or []) for key in ('target_entities','allowed_members','disease_entities','qualified_entities','related_entities','hard_excluded_entities')))
   elif isinstance(value,dict):
    counts.append(len(value))
  if derivation_failures: errors.append(f'{name} derivation fingerprint mismatch: {derivation_failures}')
  file_rows.append({'file':name,'consumers':consumers,'profile_count':len(profiles)-len(missing),'minimum_entry_count':min(counts) if counts else 0,'profile_sha256':hashes})
 prompt_rows=[]
 for name,consumers in PROMPTS.items():
  path=root/'prompts'/name
  refs=[c for c in consumers if path.is_file() and name in (root/c).read_text(encoding='utf-8')]
  if not path.is_file(): errors.append(f'prompt missing: {name}')
  if len(refs)!=len(consumers): errors.append(f'prompt not wired: {name} expected={consumers} actual={refs}')
  prompt_rows.append({'prompt':name,'purpose':name.rsplit('.',1)[0],'consumers':consumers,'wired_consumers':refs,'sha256':sha(path) if path.is_file() else ''})
 report={'policy_version':'v17.4-consumer-and-prompt-wiring-1','passed':not errors,'errors':errors,'profile_count':len(profiles),'vocabulary_files':file_rows,'prompts':prompt_rows}
 Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'passed':report['passed'],'errors':len(errors),'files':len(file_rows),'prompts':len(prompt_rows),'profiles':len(profiles)},ensure_ascii=False))
 return 0 if not errors else 1
if __name__=='__main__': raise SystemExit(main())
