import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from tools import release_check


class ReleaseTests(unittest.TestCase):
    def test_private_artifacts_and_unknown_images_are_not_exported(self):
        names = {p.relative_to(release_check.ROOT).as_posix() for p in release_check.public_files()}
        self.assertIn('.env.example', names)
        self.assertIn('assets/desktop-preview.png', names)
        self.assertIn('README.zh-CN.md', names)
        self.assertNotIn('.env', names)
        self.assertNotIn('vehicles.db', names)
        self.assertNotIn('assets/headlight_low_beam.png', names)
        self.assertFalse(any('.git/' in p or 'scratch/' in p for p in names))

    def test_nonempty_credentials_in_example_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            root = Path(tmp)
            path = root / '.env.example'
            path.write_text('TELEGRAM_CHAT_ID=example-credential', encoding='utf-8')
            with patch.object(release_check, 'ROOT', root):
                self.assertTrue(release_check.audit([path]))

    def test_real_provider_import_rejected_not_literal_in_audit(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            root = Path(tmp)
            path = root / 'monitor.py'
            # Construct the forbidden import so this test fixture is itself publishable.
            path.write_text('from fetchers.' + 'truecar' + '_fetcher import Provider', encoding='utf-8')
            with patch.object(release_check, 'ROOT', root):
                self.assertTrue(release_check.audit([path]))
