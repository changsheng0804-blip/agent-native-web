"""验证质检入口不会因参数拼写错误或别名展开而漏测、误访问真实站点。"""
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import run_quality as quality


class QualitySelectionTests(unittest.TestCase):
    def test_common_scopes_stay_offline(self):
        for scope in ('kernel', 'observer', 'fill', 'identity', 'task-runtime', 'timeline'):
            with self.subTest(scope=scope):
                targets, groups = quality.select_scope(scope)
                self.assertEqual(groups, ['offline'])
                self.assertTrue(targets)
                self.assertTrue(set(targets) <= set(quality.GROUPS['offline']['scripts']))

    def test_explicit_real_scope(self):
        targets, groups = quality.select_scope('real-site')
        self.assertIn('real', groups)
        self.assertIn('test_real_github_task_graph.py', targets)

    def test_explicit_real_flag(self):
        targets, _ = quality.select_scope('identity', include_real=True)
        self.assertIn('test_fingerprint_real.py', targets)

    def test_special_scope_is_explicit(self):
        targets, groups = quality.select_scope('cdp')
        self.assertIn('special', groups)
        self.assertEqual(targets, ['test_cdp.py'])

    def test_unknown_or_empty_scope_fails(self):
        for value in ('', ',', 'fill,typo', 'typo'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                quality.select_scope(value)

    def test_overlapping_scopes_do_not_duplicate_tests(self):
        targets, _ = quality.select_scope('kernel,observer,identity')
        self.assertEqual(len(targets), len(set(targets)))

    def test_invalid_cli_arguments_exit_before_tests(self):
        for args in (['--parallel', '0'], ['--parallel', '-1'],
                     ['--only', 'test_protocol.py', '--scope', 'kernel'],
                     ['--scope', 'fill,typo'], ['--scope', ''], ['--only', '']):
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, quality.__file__, *args],
                                        capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 2)

    def test_report_can_create_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            report = pathlib.Path(directory) / '子目录-output' / '质检-quality.md'
            with patch.object(quality, 'REPORT', report):
                quality.write_report([], [], ['offline'], time.time())
            self.assertTrue(report.is_file())


if __name__ == '__main__':
    unittest.main(verbosity=2)
