# -*- coding: utf-8 -*-
"""站点业务适配器的文件化和版本兼容性测试。"""
import unittest

from site_adapter import compare_site_adapters, load_site_adapter_file


V1 = "资料提交 profile-adapter-v1.json"
V2 = "资料提交 profile-adapter-v2.json"
GITHUB = "GitHub 开放PR筛选 github-pulls-v1.json"


class SiteAdapterTests(unittest.TestCase):
    def test_load_is_normalized_and_signed(self):
        adapter = load_site_adapter_file(V1)
        self.assertEqual(adapter["adapter_id"], "profile-adapter")
        self.assertEqual(adapter["adapter_version"], "1")
        self.assertEqual(adapter["source_file"], V1)
        self.assertEqual(len(adapter["state_rules"]), 3)
        self.assertEqual(len(adapter["operation_contracts"]), 2)
        self.assertTrue(adapter["signature"])
        github_adapter = load_site_adapter_file(GITHUB)
        self.assertEqual(len(github_adapter["state_probes"]), 3)

    def test_same_version_has_same_signature(self):
        adapter = load_site_adapter_file(V1)
        result = compare_site_adapters(adapter, adapter)
        self.assertEqual(result["status"], "same")

    def test_changed_precondition_is_incompatible(self):
        base = load_site_adapter_file(V1)
        candidate = load_site_adapter_file(V2)
        result = compare_site_adapters(base, candidate)
        self.assertEqual(result["status"], "incompatible")
        self.assertIn("提交资料", result["operations"]["changed"])
        self.assertTrue(any("site_version" in item for item in result["review_required"]))

    def test_loader_rejects_path_escape(self):
        with self.assertRaises(ValueError):
            load_site_adapter_file("..\\outside.json")


if __name__ == "__main__":
    unittest.main()
