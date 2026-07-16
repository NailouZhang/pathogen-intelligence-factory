# v2.1 translation and card logic

## Why Google did not cover every v2.0 failure

The v2.0 Python fallback called one `deep-translator` Google request and then one MyMemory request. It had no provider-level retries, no independent Google route, and no long-text chunking. A temporary request failure, provider alias mismatch, rate limit, or changed protected placeholder could therefore leave the field untranslated.

The v2.0 validator was also shared by titles and prose. Scientific titles with many Latin taxon names, gene symbols, and abbreviations could contain valid Chinese but still fail the Chinese-ratio threshold. In addition, substring glossary replacement converted plural English terms into malformed output such as `正汉坦病毒es`.

## v2.1 guarantees

- Every nonempty English title enters the full translation chain.
- Every nonempty abstract or news body enters the full translation chain independently of title translation.
- A malformed translation of one field cannot invalidate successful translations of the other fields.
- Google routes are retried and long text is chunked.
- Old v2.0 cache entries are not reused.
- Source abstract/body translation is separate from five-element interpretation.

## Audit structure

```json
{
  "translation_audit": {
    "title": {},
    "abstract_or_body": {},
    "fields": {
      "background": {},
      "methods": {}
    }
  }
}
```

Possible providers include:

- `gemini`
- `groq`
- `python_google_translate`
- `python_google_direct`
- `python_mymemory`
- `deterministic`

## Evidence policy

Translation does not create evidence. The main card paragraph translates only an existing abstract, fetched news body, or excerpt. Five-element interpretation remains evidence-bound and appears separately below it.
