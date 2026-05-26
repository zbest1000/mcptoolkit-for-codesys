# Releasing

How a new version of `mcptoolkit-for-codesys` is cut. The repo follows
[Semantic Versioning](https://semver.org) and [Conventional
Commits](https://www.conventionalcommits.org).

## Versioning at a glance

The version bump is decided by the commits since the last release tag:

| Commits since last release | Bump | Example |
|---|---|---|
| any `feat!:` / `BREAKING CHANGE:` | **major** | 0.2.1 → 1.0.0 |
| any `feat:` (no breaking) | **minor** | 0.2.1 → 0.3.0 |
| `fix:` / `perf:` only | **patch** | 0.2.1 → 0.2.2 |
| `docs:` / `chore:` / `ci:` / `test:` only | **none** — no release needed | — |

Write commit subjects in that form (`feat: …`, `fix: …`, `docs: …`) so this stays
automatic. There is exactly **one** source of version truth: `version` in
`pyproject.toml`.

## Cut a release

1. **Decide the version** from the table above. To have a tool tell you:
   ```
   git log <last-tag>..HEAD --oneline | \
     python <skill>/version_bumper.py --current-version <last-version> --input-format git-log --analysis
   ```
2. **Bump `pyproject.toml`** — set `version = "X.Y.Z"`.
3. **Update `CHANGES.md`** — move the `## [Unreleased]` notes under a new
   `## X.Y.Z` heading.
4. **Commit** on `main`:
   ```
   git commit -am "release: X.Y.Z"
   ```
5. **Tag and push** — this triggers the automated release:
   ```
   git tag vX.Y.Z
   git push origin main --tags
   ```

The [`release.yml`](.github/workflows/release.yml) workflow then runs the host
tests, checks the tag matches `pyproject.toml`, builds the wheel + sdist, and
publishes a GitHub Release with both attached. Nothing else to do.

## Release checklist

Before tagging:

- [ ] `pytest` is green locally (`pip install -e ".[dev]" && pytest`).
- [ ] `pyproject.toml` version bumped.
- [ ] `CHANGES.md` updated (move `[Unreleased]` → the new version).
- [ ] No machine-specific paths in shipped files (the repo uses generic
      placeholders; double-check anything new).
- [ ] `python -m build && twine check dist/*` passes (the workflow also does this).

## Doing it fully by hand (fallback)

If you'd rather not use the workflow, after steps 1–4 above:

```
python -m build
twine check dist/*
gh release create vX.Y.Z dist/* --title "X.Y.Z" --notes-file CHANGES.md
```

## Hotfix

For an urgent fix to an already-released version: branch from the release tag,
make the minimal `fix:` commit, bump the patch version, then tag `vX.Y.Z+1` as
above. Avoid bundling unrelated changes into a hotfix.
