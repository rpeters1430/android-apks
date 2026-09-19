#!/usr/bin/env python3
"""Validate definitions and build a signed Morphe-patched APK."""

from __future__ import annotations

import argparse, base64, datetime as dt, hashlib, json, os, re, shutil, subprocess, sys
import urllib.error, urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_RE = re.compile(r"^[a-fA-F0-9]{64}$")


class HubError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HubError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HubError(f"{path} must contain a JSON object")
    return value


def validate_release_source(source: Any, location: str, apk: bool = False) -> None:
    if not isinstance(source, dict):
        raise HubError(f"{location} must be an object")
    if apk and source.get("type") not in ("github-release", "manual"):
        raise HubError(f"{location}.type must be github-release or manual")
    if apk and source["type"] == "manual":
        return  # supplied at run time via workflow inputs; nothing else to validate
    if not isinstance(source.get("repo"), str) or not REPO_RE.fullmatch(source["repo"]):
        raise HubError(f"{location}.repo must be owner/repository")
    pattern = source.get("asset_regex")
    if not isinstance(pattern, str) or not pattern:
        raise HubError(f"{location}.asset_regex must be a non-empty string")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise HubError(f"{location}.asset_regex is invalid: {exc}") from exc
    if "sha256" in source and (
        not isinstance(source["sha256"], str) or not SHA_RE.fullmatch(source["sha256"])
    ):
        raise HubError(f"{location}.sha256 must be 64 hexadecimal characters")


def validate_app(app: dict[str, Any], path: Path) -> None:
    app_id = app.get("id")
    if not isinstance(app_id, str) or not ID_RE.fullmatch(app_id):
        raise HubError(f"{path}: id must be a lowercase URL-safe identifier")
    if path.stem != app_id:
        raise HubError(f"{path}: filename must be {app_id}.json")
    for field in ("name", "package"):
        if not isinstance(app.get(field), str) or not app[field].strip():
            raise HubError(f"{path}: {field} must be a non-empty string")
    for field in ("enabled", "redistribute"):
        if not isinstance(app.get(field), bool):
            raise HubError(f"{path}: {field} must be true or false")
    if app["enabled"] and not app["redistribute"]:
        raise HubError(f"{path}: enabled apps must explicitly set redistribute to true")
    validate_release_source(app.get("source"), f"{path}: source", apk=True)
    patches = app.get("patches")
    if not isinstance(patches, list) or not patches:
        raise HubError(f"{path}: patches must be a non-empty array")
    for index, source in enumerate(patches):
        validate_release_source(source, f"{path}: patches[{index}]")
    publish = app.get("publish", {})
    if not isinstance(publish, dict):
        raise HubError(f"{path}: publish must be an object")
    if "repo" in publish and (not isinstance(publish["repo"], str) or not REPO_RE.fullmatch(publish["repo"])):
        raise HubError(f"{path}: publish.repo must be owner/repository")
    selection = app.get("selection", {})
    if not isinstance(selection, dict):
        raise HubError(f"{path}: selection must be an object")
    if not isinstance(selection.get("exclusive", False), bool):
        raise HubError(f"{path}: selection.exclusive must be true or false")
    for field in ("enable", "disable"):
        values = selection.get(field, [])
        if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
            raise HubError(f"{path}: selection.{field} must be an array of patch names")


def load_apps() -> dict[str, dict[str, Any]]:
    apps: dict[str, dict[str, Any]] = {}
    for path in sorted((ROOT / "apps").glob("*.json")):
        app = read_json(path)
        validate_app(app, path)
        if app["id"] in apps:
            raise HubError(f"Duplicate app id: {app['id']}")
        app["_path"] = str(path.relative_to(ROOT))
        apps[app["id"]] = app
    if not apps:
        raise HubError("No app definitions found in apps/")
    return apps


def validate_all() -> None:
    config = read_json(ROOT / "config.json")
    if config.get("schema_version") != 1:
        raise HubError("config.json: unsupported schema_version")
    validate_release_source(config.get("morphe_desktop"), "config.json: morphe_desktop")
    print(f"Validated {len(load_apps())} app definition(s)")


def api_json(url: str, token: str | None) -> dict[str, Any]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "android-apk-patch-hub/1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.load(response)
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise HubError(f"GitHub request failed for {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise HubError(f"Unexpected GitHub response for {url}")
    return data


def release_asset(source: dict[str, Any], token: str | None) -> dict[str, str]:
    repo = source["repo"]
    selector = f"tags/{source['tag']}" if source.get("tag") else "latest"
    release = api_json(f"https://api.github.com/repos/{repo}/releases/{selector}", token)
    pattern = re.compile(source["asset_regex"])
    matches = [a for a in release.get("assets", []) if pattern.fullmatch(a.get("name", ""))]
    if len(matches) != 1:
        names = ", ".join(a.get("name", "") for a in release.get("assets", []))
        raise HubError(f"{repo}@{release.get('tag_name', selector)}: pattern matched {len(matches)} assets; available: {names or '(none)'}")
    asset = matches[0]
    return {"repo": repo, "release_tag": str(release.get("tag_name", "")), "name": asset["name"], "url": asset["browser_download_url"]}


def safe_manual_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise HubError("Manual APK URL must be an absolute HTTPS URL")
    if parsed.username or parsed.password or parsed.query:
        raise HubError("Manual APK URL must not contain credentials or a query string")
    return value


def download(url: str, destination: Path, expected_sha: str | None = None) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "android-apk-patch-hub/1"})
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=180) as response, destination.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk); digest.update(chunk)
    except urllib.error.URLError as exc:
        raise HubError(f"Download failed for {url}: {exc}") from exc
    actual = digest.hexdigest()
    if expected_sha and actual.lower() != expected_sha.lower():
        destination.unlink(missing_ok=True)
        raise HubError(f"SHA-256 mismatch for {url}: expected {expected_sha}, got {actual}")
    return actual


def run_checked(command: list[str]) -> None:
    # Never print secret-bearing arguments.
    print(f"+ {command[0]} …", flush=True)
    subprocess.run(command, check=True)


def android_tool(name: str) -> str | None:
    """Find an Android SDK tool even when build-tools is not on PATH."""
    if found := shutil.which(name):
        return found
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if root := os.environ.get(variable):
            matches = sorted(Path(root).glob(f"build-tools/*/{name}"), reverse=True)
            if matches:
                return str(matches[0])
    return None


def apk_badging(apk: Path) -> tuple[str, str] | None:
    """Return (package, versionName) from the APK manifest, or None if aapt2 is unavailable."""
    aapt2 = android_tool("aapt2")
    if not aapt2: return None
    text = subprocess.run([aapt2, "dump", "badging", str(apk)], capture_output=True, text=True, check=False).stdout
    match = re.search(r"package: name='([^']+)'.*?versionName='([^']*)'", text)
    return (match.group(1), match.group(2)) if match else None


def supported_versions(desktop: Path, patches: list[Path], package: str) -> set[str]:
    """App versions the patch bundles declare support for (empty set = no version restriction)."""
    command = ["java", "-jar", str(desktop), "list-patches", "-p", "-v", "-d=false", "-i=false", "-f", package]
    for path in patches: command.extend(["--patches", str(path)])
    output = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    return parse_supported_versions(output, package)


def parse_supported_versions(output: str, package: str) -> set[str]:
    versions, in_package, in_versions = set(), False, False
    for line in re.sub(r"\x1b\[[0-9;]*m", "", output).splitlines():
        stripped = line.strip()
        if stripped.startswith("Package name:"): in_package, in_versions = stripped.split(":", 1)[1].strip() == package, False
        elif stripped.startswith("Compatible versions:"): in_versions = in_package
        elif stripped.startswith("Name:"): in_package = in_versions = False
        elif in_versions and stripped: versions.add(stripped)
    return versions


def slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")[:80] or "unknown"


def write_output(name: str, value: str) -> None:
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")
    print(f"{name}={value}")


def build(args: argparse.Namespace) -> None:
    validate_all(); apps = load_apps()
    if args.app not in apps:
        raise HubError(f"Unknown app id {args.app!r}; choose one of: {', '.join(apps)}")
    app = apps[args.app]
    if not app["enabled"] and not args.allow_disabled:
        raise HubError(f"{args.app} is disabled")
    work = Path(args.work_dir).resolve()
    if work.exists(): shutil.rmtree(work)
    work.mkdir(parents=True)
    token = os.environ.get("GITHUB_TOKEN")
    config = read_json(ROOT / "config.json")

    desktop_meta = release_asset(config["morphe_desktop"], token)
    desktop = work / "morphe-desktop.jar"
    desktop_meta["sha256"] = download(desktop_meta["url"], desktop, config["morphe_desktop"].get("sha256"))
    if app["source"]["type"] == "manual" and not args.upstream_apk_url:
        raise HubError(f"{app['id']} uses a manual source; supply --upstream-apk-url (and --upstream-version)")
    if args.upstream_apk_url:
        upstream_meta = {"repo": "manual", "release_tag": args.upstream_version or "manual", "name": f"{app['id']}-upstream.apk", "url": safe_manual_url(args.upstream_apk_url)}
        expected_apk_sha = args.upstream_sha256
    else:
        upstream_meta = release_asset(app["source"], token); expected_apk_sha = app["source"].get("sha256")
    upstream = work / "upstream.apk"
    upstream_meta["sha256"] = download(upstream_meta["url"], upstream, expected_apk_sha)

    patch_paths, patch_meta = [], []
    for index, source in enumerate(app["patches"]):
        metadata = release_asset(source, token); path = work / f"patches-{index}.mpp"
        metadata["sha256"] = download(metadata["url"], path, source.get("sha256"))
        patch_paths.append(path); patch_meta.append(metadata)

    badging = apk_badging(upstream)
    if badging:
        package, version = badging
        if package != app["package"]:
            raise HubError(f"Upstream APK is {package}, but {app['id']} expects {app['package']}")
        if upstream_meta["release_tag"] == "manual": upstream_meta["release_tag"] = version
        elif upstream_meta["release_tag"] != version: print(f"::warning::supplied version {upstream_meta['release_tag']} differs from APK versionName {version}")
        upstream_meta["version_name"] = version
    else: print("::warning::aapt2 not found; package/version of the upstream APK not verified")
    tested = upstream_meta.get("version_name") or upstream_meta["release_tag"]
    allowed = supported_versions(desktop, patch_paths, app["package"])
    if allowed and tested not in allowed and not args.allow_unsupported_version:
        raise HubError(f"{app['package']} {tested} is not supported by the patches; supported: {', '.join(sorted(allowed))}")

    required = ["ANDROID_KEYSTORE_BASE64", "ANDROID_KEYSTORE_PASSWORD", "ANDROID_KEY_ALIAS", "ANDROID_KEY_PASSWORD"]
    missing = [key for key in required if not os.environ.get(key)]
    if missing: raise HubError(f"Missing signing secrets: {', '.join(missing)}")
    keystore = work / "signing.jks"
    try:
        keystore.write_bytes(base64.b64decode(os.environ["ANDROID_KEYSTORE_BASE64"], validate=True))
    except ValueError as exc:
        raise HubError("ANDROID_KEYSTORE_BASE64 is not valid base64") from exc

    output, result = work / f"{app['id']}-patched.apk", work / "morphe-result.json"
    command = ["java", "-Xms1G", "-Xmx4G", "-jar", str(desktop), "patch"]
    selection = app.get("selection", {})
    for index, path in enumerate(patch_paths):
        command.extend(["--patches", str(path)])
        if index == 0:
            for patch in selection.get("enable", []): command.extend(["--enable", patch])
            for patch in selection.get("disable", []): command.extend(["--disable", patch])
    if selection.get("exclusive", False): command.append("--exclusive")
    command.extend(["--out", str(output), "--result-file", str(result), "--keystore", str(keystore), "--keystore-password", os.environ["ANDROID_KEYSTORE_PASSWORD"], "--keystore-entry-alias", os.environ["ANDROID_KEY_ALIAS"], "--keystore-entry-password", os.environ["ANDROID_KEY_PASSWORD"], str(upstream)])
    try: run_checked(command)
    finally: keystore.unlink(missing_ok=True)
    if not output.is_file(): raise HubError("Morphe produced no APK")
    if apksigner := android_tool("apksigner"):
        run_checked([apksigner, "verify", "--verbose", "--print-certs", str(output)])
    else: print("::warning::apksigner not found; independent signature verification skipped")

    output_sha = hashlib.sha256(output.read_bytes()).hexdigest()
    config_sha = hashlib.sha256(json.dumps({k: v for k, v in app.items() if k != "_path"}, sort_keys=True).encode()).hexdigest()
    build_info = {"schema_version": 1, "app": {"id": app["id"], "name": app["name"], "package": app["package"]}, "built_at": dt.datetime.now(dt.timezone.utc).isoformat(), "repository_commit": os.environ.get("GITHUB_SHA", "local"), "configuration_sha256": config_sha, "upstream": upstream_meta, "morphe_desktop": desktop_meta, "patch_bundles": patch_meta, "patched_apk": {"name": output.name, "sha256": output_sha}}
    info_path, checksums = work / "build-info.json", work / "checksums.txt"
    info_path.write_text(json.dumps(build_info, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checksums.write_text(f"{output_sha}  {output.name}\n", encoding="utf-8")
    combined = hashlib.sha256((upstream_meta["sha256"] + "".join(p["sha256"] for p in patch_meta) + config_sha).encode()).hexdigest()[:12]
    tag = f"{app['id']}-{slug(upstream_meta['release_tag'])}-{combined}"
    for name, value in {"tag": tag, "app_name": app["name"], "version": upstream_meta["release_tag"], "apk": output, "build_info": info_path, "checksums": checksums, "morphe_result": result}.items(): write_output(name, str(value))


def matrix(app: str | None) -> None:
    apps = load_apps()
    if app and app not in apps: raise HubError(f"Unknown app id {app!r}")
    # Scheduled runs skip manual-source apps: they cannot fetch their own upstream APK.
    selected = [app] if app else [key for key, value in apps.items() if value["enabled"] and value["source"]["type"] != "manual"]
    print(json.dumps({"include": [{"app": key, "release_repo": apps[key].get("publish", {}).get("repo", "")} for key in selected]}, separators=(",", ":")))


def obtainium(repository: str) -> None:
    """Print an Obtainium import file for every enabled app, pointing at its release feed."""
    entries = []
    for app in load_apps().values():
        if not app["enabled"]: continue
        repo = app.get("publish", {}).get("repo") or repository
        settings = {"includePrereleases": False, "apkFilterRegEx": f"^{re.escape(app['id'])}-patched\\.apk$", "trackOnly": False}
        entries.append({"id": app.get("patched_package", app["package"]), "url": f"https://github.com/{repo}", "author": repo.split("/")[0], "name": app["name"], "preferredApkIndex": 0, "additionalSettings": json.dumps(settings)})
    print(json.dumps({"apps": entries}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    matrix_parser = commands.add_parser("matrix"); matrix_parser.add_argument("--app")
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--app", required=True); build_parser.add_argument("--work-dir", required=True)
    build_parser.add_argument("--upstream-apk-url"); build_parser.add_argument("--upstream-version"); build_parser.add_argument("--upstream-sha256"); build_parser.add_argument("--allow-disabled", action="store_true"); build_parser.add_argument("--allow-unsupported-version", action="store_true")
    obtainium_parser = commands.add_parser("obtainium"); obtainium_parser.add_argument("--repository", required=True, help="owner/repo hosting the releases")
    args = parser.parse_args()
    try:
        if args.command == "validate": validate_all()
        elif args.command == "matrix": matrix(args.app)
        elif args.command == "obtainium": obtainium(args.repository)
        else: build(args)
    except (HubError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main())
