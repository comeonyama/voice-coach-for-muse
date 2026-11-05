import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from poll_diagnostics import summary

class PollDiagnosticsTests(unittest.TestCase):
    def test_redaction_and_bounds(self):
        result = summary({'token':'private', 'error':'https://host/token/private failed private',
                          'nested':{'authorization':'secret'}, 'stderr':'x'*10000}, ('private',))
        self.assertNotIn('private', result)
        self.assertNotIn('https://', result)
        self.assertNotIn('"secret"', result)
        self.assertLessEqual(len(result), 6000)
