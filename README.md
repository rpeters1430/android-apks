# Android APK Patch Hub

Build, sign, and publish reproducible patched Android APKs with GitHub Actions. The
released APKs can be followed by [Obtainium](https://github.com/ImranR98/Obtainium).

The first patch engine is [Morphe Desktop](https://github.com/MorpheApp/morphe-desktop).
App definitions are data files, so additional apps and trusted patch bundles do not
require another workflow.

> [!IMPORTANT]
> Only redistribute APKs when the application's license permits it. For proprietary
> apps, publish patch bundles/configuration or use the manual workflow with an APK URL
> you are authorized to use. You are responsible for the applications and patches you
> configure.

## What it does

- checks configured GitHub releases once per day;
- downloads an upstream APK and one or more pinned or latest `.mpp` bundles;
- records URLs, release tags, commit/config hash, and SHA-256 hashes;
- patches and signs with one stable Android signing identity;
- verifies the APK signature;
- publishes the APK, checksums, Morphe result, and `build-info.json` in a GitHub Release;
- skips an existing identical release unless a rebuild is explicitly requested.

No patched apps are enabled by default. Copy `apps/example.json`, choose an app whose
license allows redistribution, validate it, and open a pull request.

## Initial setup

### 1. Create a permanent signing key

Run this once on a trusted computer (not in Actions):

```bash
keytool -genkeypair -v \
  -keystore android-apks.jks \
  -alias android-apks \
  -keyalg RSA -keysize 4096 -validity 10000
```

Never replace or lose this key. Android only accepts an update when it is signed by
the same key as the installed APK.

### 2. Add GitHub Actions secrets

Base64-encode the keystore as a single line:

```bash
base64 -w 0 android-apks.jks
```

On macOS, use `base64 < android-apks.jks | tr -d '\n'`.

Add these repository secrets under **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | Output of the base64 command |
| `ANDROID_KEYSTORE_PASSWORD` | Keystore password |
| `ANDROID_KEY_ALIAS` | `android-apks` (or the alias you chose) |
| `ANDROID_KEY_PASSWORD` | Key password |

The workflow uses the built-in `GITHUB_TOKEN`; no personal access token is needed for
public GitHub release sources or publishing releases in this repository.

### 3. Add an app

Copy the example and edit it:

```bash
cp apps/example.json apps/my-app.json
python3 scripts/patch_hub.py validate
```

The safest automatic source is a public GitHub release:

```json
{
  "id": "my-app",
  "name": "My App",
  "package": "com.example.app",
  "enabled": true,
  "redistribute": true,
  "source": {
    "type": "github-release",
    "repo": "owner/repository",
    "asset_regex": "^my-app-[0-9.]+\\.apk$"
  },
  "patches": [
    {
      "repo": "MorpheApp/morphe-patches",
      "asset_regex": "^patches-[0-9.]+\\.mpp$"
    }
  ],
  "selection": {
    "exclusive": false,
    "enable": [],
    "disable": []
  }
}
```

`asset_regex` must match exactly one asset in the selected release. A source or patch
entry may include `tag` to pin a release instead of using `latest`, and `sha256` to pin
the downloaded bytes.

## Running it

- **Automatic:** enabled apps are checked daily at 12:17 UTC.
- **Manual:** Actions → **Build patched APK** → Run workflow. Select an app ID.
- **Authorized one-off source:** supply `upstream_apk_url` in the manual form. The URL
  overrides the configured app source for that run.
- **Test without publishing:** set `publish_release` to `false`; artifacts remain in
  the workflow run for seven days.

Manual URLs must use HTTPS. Secrets embedded in URLs are rejected because URLs are
recorded in build metadata and can appear in logs.

## Obtainium

After the first GitHub Release is published:

1. Add `https://github.com/rpeters1430/android-apks` in Obtainium.
2. In the app's release-asset filter, select the app-specific APK name, such as
   `my-app-patched.apk`.
3. Enable prereleases only if you deliberately publish prerelease builds.

Because all builds use the same repository keystore, Obtainium can install subsequent
versions over earlier builds. A patched APK generally cannot replace the vendor APK
unless both package name and signing certificate are compatible; uninstall conflicts
or use a patch that changes the package name.

## Security model

- Patch sources are explicit per app; the hub does not discover or trust arbitrary
  community repositories.
- Downloads and exact hashes are recorded in each release.
- Optional `sha256` fields turn source checks into hard verification gates.
- Actions have read-only permissions except the build job's `contents: write` release
  permission.
- Shell commands do not evaluate configuration values.
- Keystore material is decoded only into the runner's temporary directory.

See [SECURITY.md](SECURITY.md) before adding a community patch source.

## Development

```bash
python3 scripts/patch_hub.py validate
python3 -m unittest discover -s tests -v
```

The implementation uses Python's standard library, so validation requires no package
installation.

## Proprietary apps (YouTube, YouTube Music, Reddit)

`apps/youtube.json`, `apps/youtube-music.json` and `apps/reddit.json` use
`"source": {"type": "manual"}`. There is no upstream release feed, so:

- scheduled runs skip them; run **Build patched APK** manually with `upstream_apk_url`
  (an HTTPS APK you are authorized to use) whenever you want a new build;
- the build reads package and version from the APK (needs `aapt2`, present on GitHub
  runners), rejects a package mismatch, and rejects versions the patch bundle does not
  list as supported (override with `allow_unsupported_version`);
- the APK must be a single `.apk`, not an `.apkm`/`.xapk` bundle.

Their `publish.repo` sends releases to a separate **private** repository. Create it, and add
a fine-grained token with *Contents: read and write* on that repo as the `PUBLISH_TOKEN`
secret. Edit `publish.repo` to match your repository name. To follow a private repo,
Obtainium needs a GitHub token in its settings.

Patched YouTube/YT Music installs without root generally need
[MicroG-RE](https://github.com/MorpheApp/MicroG-RE) installed as well.

### Obtainium import file

```bash
python3 scripts/patch_hub.py obtainium --repository rpeters1430/android-apks > obtainium.json
```

This lists every enabled app with an `apkFilterRegEx` for its `<id>-patched.apk`. If the
patched app has a different package name than the original, set `patched_package` in its
app definition.
