# API Contract

`openapi.json` is the canonical, versioned backend HTTP contract. Regenerate it after an
intentional API change:

```bash
uv run python scripts/export_openapi.py
```

Check for drift without changing the artifact:

```bash
uv run python scripts/export_openapi.py --check
```

The frontend is an independent repository. Its generated client consumes this committed artifact;
backend CI verifies the source contract but does not implicitly edit or validate the sibling
frontend repository.
