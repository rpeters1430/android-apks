import importlib.util
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location("patch_hub", Path(__file__).parents[1] / "scripts" / "patch_hub.py")
patch_hub = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(patch_hub)


class ValidationTests(unittest.TestCase):
    def app(self):
        return {
            "id": "test-app", "name": "Test App", "package": "org.example.test",
            "enabled": False, "redistribute": False,
            "source": {"type": "github-release", "repo": "owner/repo", "asset_regex": r"^app\.apk$"},
            "patches": [{"repo": "owner/patches", "asset_regex": r"^patches\.mpp$"}],
            "selection": {"exclusive": False, "enable": [], "disable": []},
        }

    def test_valid_app(self):
        patch_hub.validate_app(self.app(), Path("test-app.json"))

    def test_enabled_requires_redistribution_acknowledgement(self):
        app = self.app(); app["enabled"] = True
        with self.assertRaisesRegex(patch_hub.HubError, "redistribute"):
            patch_hub.validate_app(app, Path("test-app.json"))

    def test_filename_must_match_id(self):
        with self.assertRaisesRegex(patch_hub.HubError, "filename"):
            patch_hub.validate_app(self.app(), Path("wrong.json"))

    def test_manual_url_rejects_query_secrets(self):
        with self.assertRaisesRegex(patch_hub.HubError, "query"):
            patch_hub.safe_manual_url("https://example.com/app.apk?token=secret")

    def test_manual_source_needs_no_repo(self):
        app = self.app(); app["source"] = {"type": "manual"}
        patch_hub.validate_app(app, Path("test-app.json"))

    def test_publish_repo_must_be_owner_slash_name(self):
        app = self.app(); app["publish"] = {"repo": "not a repo"}
        with self.assertRaisesRegex(patch_hub.HubError, "publish.repo"):
            patch_hub.validate_app(app, Path("test-app.json"))

    def test_parse_supported_versions_for_one_package(self):
        output = chr(10).join([
            "Name: Hide ads", "Enabled: true", "Compatible packages:",
            "	Package name: com.a", "	Compatible versions:", "		1.0", "		2.0", "",
            "Name: Other", "Enabled: true", "Compatible packages:",
            "	Package name: com.b", "	Compatible versions:", "		9.9",
        ])
        self.assertEqual({"1.0", "2.0"}, patch_hub.parse_supported_versions(output, "com.a"))

    def test_slug_is_release_safe(self):
        self.assertEqual("v1.2-beta", patch_hub.slug("v1.2 beta"))


if __name__ == "__main__": unittest.main()
