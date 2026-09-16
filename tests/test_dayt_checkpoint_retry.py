import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Stragety/MiniQMT_Stragety'))
from core import dayt_checkpoint as checkpoint


def denied(code=5):
    error = PermissionError('file temporarily locked')
    error.winerror = code
    return error


class CheckpointRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        checkpoint.write_checkpoint(self.path, {'schema': 1, 'account': 'test', 'value': 'old'})
        self.data = {'schema': 1, 'account': 'test', 'value': 'new'}

    def test_transient_denial_retries_same_temporary_then_succeeds(self):
        original = checkpoint.os.replace
        calls = []
        def replace(source, target):
            calls.append((source, target))
            if len(calls) < 3:
                self.assertEqual(checkpoint.read_checkpoint(self.path, 'test')['value'], 'old')
                raise denied()
            original(source, target)
        with patch.object(checkpoint.os, 'replace', side_effect=replace), \
                patch('time.sleep') as sleep:
            checkpoint.write_checkpoint(self.path, self.data)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(set(str(source) for source, _ in calls)), 1)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(checkpoint.read_checkpoint(self.path, 'test')['value'], 'new')

    def test_persistent_denial_is_bounded_and_keeps_old_file(self):
        with patch.object(checkpoint.os, 'replace', side_effect=denied(32)) as replace, \
                patch('time.sleep') as sleep:
            with self.assertRaises(PermissionError):
                checkpoint.write_checkpoint(self.path, self.data)
        self.assertEqual(replace.call_count, 5)
        self.assertEqual(sleep.call_count, 4)
        self.assertEqual(checkpoint.read_checkpoint(self.path, 'test')['value'], 'old')
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_other_io_error_is_not_retried(self):
        with patch.object(checkpoint.os, 'replace', side_effect=OSError('disk failure')) as replace, \
                patch('time.sleep') as sleep:
            with self.assertRaises(OSError):
                checkpoint.write_checkpoint(self.path, self.data)
        replace.assert_called_once()
        sleep.assert_not_called()

    def test_checkpoint_json_is_indented_for_human_reading(self):
        checkpoint.write_checkpoint(
            self.path,
            {'schema': 1, 'account': 'test', 'nested': {'value': 3}})

        text = self.path.read_text(encoding='utf-8')

        self.assertIn('\n  "account": "test",\n', text)
        self.assertIn('\n  "nested": {\n    "value": 3\n  }\n', text)
        self.assertTrue(text.endswith('\n'))


if __name__ == '__main__':
    unittest.main()
