# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Android APK Patch Hub: GitHub Actions builds, signs, and publishes patched Android APKs (via [Morphe Desktop](https://github.com/MorpheApp/morphe-desktop) and `.mpp` patch bundles) as GitHub Releases that Obtainium can follow. Apps are defined as data files, so adding an app never requires a workflow change. The project is at an early stage: Morphe patches (and Morphe Desktop as the only patch engine) come first, with other community patch sources and engines planned later, so keep the app/patch-source definitions engine-agnostic where practical. No patched apps are enabled by default.

## Commands

The implementation is pure Python standard library (no installs needed).

```bash
python3 scripts/patch_hub.py validate                 # validate config.json + apps/*.json
python3 scripts/patch_hub.py matrix [--app ID]        # print the GitHub Actions matrix JSON
python3 -m unittest discover -s tests -v              # run all tests
python3 -m unittest tests.test_patch_hub.ValidationTests.test_valid_app   # single test
```

`patch_hub.py obtainium --repository owner/repo` prints an Obtainium import file. On Windows use `python` (the `python3` alias is a Store stub).

`build` (`patch_hub.py build --app ID --work-dir DIR [--allow-disabled] [--upstream-apk-url ... --upstream-version ... --upstream-sha256 ...]`) needs Java, network access, and the `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD` env vars; it is normally run only by CI.

## Architecture

- `scripts/patch_hub.py` is the single implementation file, with subcommands `validate`, `matrix`, `build`. Code style is dense (multiple statements per line in places); match it.
- `config.json` pins the Morphe Desktop release (repo + `asset_regex`, optional `sha256`).
- `apps/<id>.json` defines one app: `source` (upstream APK, a `github-release`), `patches` (list of bundle release sources), and `selection` (`exclusive`/`enable`/`disable` patch names, applied only to the first bundle). `apps/example.json` is the template.
- App `source.type` is `github-release` (automatic) or `manual` (proprietary apps: APK URL supplied at dispatch, skipped by scheduled runs). Optional `publish.repo` releases to another (private) repo using the `PUBLISH_TOKEN` secret. `build` checks the APK's package/version via `aapt2` and the patch bundle's supported versions (`list-patches -p -v`).
- Release sources are resolved by `release_asset`: `asset_regex` must match exactly one asset of the `latest` (or pinned `tag`) release; optional `sha256` makes the download a hard verification gate.
- `build` flow: validate -> download Morphe jar, upstream APK, patch bundles -> decode keystore into the work dir (deleted afterwards) -> run `java -jar morphe-desktop patch` -> `apksigner verify` if available -> write `build-info.json`, `checksums.txt`, and step outputs. The release tag is `<app-id>-<upstream-version>-<12-char hash>` where the hash covers upstream, bundle, and app-config SHA-256s, so an identical build maps to an existing tag and is skipped.
- `.github/workflows/build.yml`: `plan` job runs `matrix` to produce the build matrix; `build` job runs per app, uploads artifacts, and publishes a release only if the tag doesn't already exist (`force_rebuild` replaces assets). Scheduled runs (daily 12:17 UTC) build enabled apps only; manual dispatch may use `--allow-disabled` and an HTTPS `upstream_apk_url` override. `validate.yml` runs on PRs/pushes to main.

## Conventions and constraints

- Validation invariants (see `validate_app`): filename stem must equal `id`; `enabled: true` requires `redistribute: true`; `patches` must be non-empty. Keep `tests/test_patch_hub.py` in sync when changing them.
- Workflow actions are pinned by commit SHA (Dependabot/Renovate update them); keep that when editing workflows.
- Never let config values reach shell evaluation; pass them via `env:` and quoted variables as the existing workflow does.
- The signing keystore is permanent; changing it breaks in-place updates for installed users. See `SECURITY.md` before adding community patch sources.
