"""核对原始业务资料之间的引用与记录形式。"""

import unittest

from inspect_samples import read_samples


class SampleTests(unittest.TestCase):
    def setUp(self):
        self.data = read_samples()

    def test_role_and_trip_keys(self):
        households = {row['id']: row for row in self.data['frame']}
        links = {(row['household'], row['trip']) for row in self.data['diary']}
        for row in self.data['events']:
            self.assertIn(row['person'], households[row['household']]['persons'])
            self.assertIn((row['household'], row['trip']), links)
        self.assertLess(len({trip for _, trip in links}), len(links))

    def test_late_response_and_cross_day_diaries(self):
        self.assertTrue(any(row['received'] > self.data['policy']['initial_cutoff'] for row in self.data['diary']))
        self.assertTrue(any(row['end'] is not None and row['start'] < 1440 < row['end'] for row in self.data['events']))
