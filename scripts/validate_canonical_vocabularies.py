#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path

from pifactory.bundled_vocabulary import validate_bundled_vocabulary


def main() -> int:
    ap=argparse.ArgumentParser(description='Execute production semantic validation for every canonical pathogen vocabulary.')
    ap.add_argument('--project-root',default='.')
    ap.add_argument('--output',default='CANONICAL_VOCABULARY_VALIDATION.json')
    args=ap.parse_args()
    root=Path(args.project_root).resolve(); rows=[]; failed=0
    for d in sorted((root/'config'/'vocabularies').iterdir()):
        if not d.is_dir(): continue
        ok,errors,manifest=validate_bundled_vocabulary(root,d.name,semantic=True)
        canonical=json.loads((d/'canonical_vocabulary.json').read_text(encoding='utf-8'))
        cases=json.loads((d/'validation_cases.json').read_text(encoding='utf-8'))
        embedded=canonical.get('validation_cases') or {}
        keys=("schema_version","profile_id","positive","negative","related","comparison")
        comparable={key:cases.get(key) for key in keys}
        embedded_comparable={key:embedded.get(key) for key in keys}
        if comparable != embedded_comparable:
            ok=False; errors=list(errors)+['validation_cases.json diverges from canonical_vocabulary.json']
        if cases.get('derived_from_semantic_fingerprint') != canonical.get('semantic_fingerprint'):
            ok=False; errors=list(errors)+['validation_cases.json missing canonical derivation fingerprint']
        row={
            'profile_id':d.name,'passed':ok,'errors':errors,
            'positive_cases':len(cases.get('positive') or []),
            'negative_cases':len(cases.get('negative') or []),
            'related_cases':len(cases.get('related') or []),
            'comparison_cases':len(cases.get('comparison') or []),
            'bundle_version':manifest.get('bundle_version'),
        }
        rows.append(row); failed += 0 if ok else 1
    report={
        'schema_version':2,'policy_version':'v17.4-production-tiered-semantic-cases-1',
        'profiles':len(rows),'passed':len(rows)-failed,'failed':failed,
        'case_count':sum(x['positive_cases']+x['negative_cases']+x['related_cases']+x['comparison_cases'] for x in rows),
        'results':rows,
    }
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('profiles','passed','failed','case_count')},ensure_ascii=False))
    return 1 if failed else 0
if __name__=='__main__': raise SystemExit(main())
