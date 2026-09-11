import json
import unittest
import zipfile
from backtest.dayt_benchmark import GOLDEN,verify,EXPECTED


class GoldenTests(unittest.TestCase):
    def test_archive_hashes_and_expected_trade_counts(self):
        verify()
        with zipfile.ZipFile(GOLDEN/'snapshot.zip') as archive:
            rows=json.loads(archive.read('analysis/v51_v39_minute/results.json'))['results']
        dates=sorted({row['date'] for row in rows})
        self.assertEqual((len(dates),dates[0],dates[-1]),(99,'20260421','20260910'))
        for version,expected in EXPECTED.items():
            selected=[row for row in rows if row['version']==version and row['slip']==0]
            self.assertEqual(sum(len(row['trades']) for row in selected),expected['trades'])
            self.assertAlmostEqual(sum(row['excess_gross'] for row in selected),expected['excess'])


if __name__=='__main__': unittest.main()
