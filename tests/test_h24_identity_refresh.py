"""H24 M-4: automatic player/weapon names must refresh with the live collection.

``IdentityStore._collection()`` used to cache the first-seen imported name into
``self.defaults`` (guarded by ``if key not in self.defaults``), so a re-import
with a changed or removed name stayed stale until restart and read paths
mutated committed state.  These tests pin the derived-fresh contract:
committed ``identity_defaults.json`` entries and every user override (explicit
empty/null included) keep winning over the automatic name.  Synthetic rows
only; nothing touches real assets, the server or the GUI.
"""
import copy
import tempfile
import unittest
from pathlib import Path

from hangar.identities import IdentityStore, load_defaults

ROWS = [
    {'kind': 'weapon', 'resource_id': 'WP_R_4010', 'name': 'EARSHOT'},
    {'kind': 'player', 'resource_id': 'HD_M_3010', 'name': 'RAVEN'},
    {'kind': 'enemy', 'resource_id': 'C4200'},
]


class IdentityRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='H24 identity refresh ')
        self.dir = Path(self.tmp.name)
        self.rows = copy.deepcopy(ROWS)
        self.store = IdentityStore(self.dir, lambda: self.rows)

    def tearDown(self):
        self.tmp.cleanup()

    def name_en(self, kind, resource_id):
        return self.store.record(kind, resource_id)['names']['en']

    def rename(self, resource_id, new_name):
        for row in self.rows:
            if row['resource_id'] == resource_id:
                row['name'] = new_name

    def test_reimport_changed_name_refreshes_automatic_name(self):
        self.store.catalog()  # the stale path froze the name on first read
        self.rename('WP_R_4010', 'MACHINEGUN 303B')
        self.assertEqual(self.name_en('weapon', 'WP_R_4010'), 'MACHINEGUN 303B')
        row = next(r for r in self.store.catalog()['identities']
                   if r['key'] == 'weapon:WP_R_4010')
        self.assertEqual(row['display_name'], 'MACHINEGUN 303B')

    def test_reimport_removed_name_drops_automatic_name(self):
        self.store.catalog()
        self.rename('HD_M_3010', 'HD_M_3010')  # name no longer differs from ID
        record = self.store.record('player', 'HD_M_3010')
        self.assertIsNone(record['names']['en'])
        self.assertEqual(record['display_name'], 'HD_M_3010')
        self.assertEqual(record['status'], 'pending')
        self.assertIn('names.en', record['pending_fields'])

    def test_reads_never_mutate_committed_defaults(self):
        committed = load_defaults()
        snapshot = copy.deepcopy(self.store.defaults)
        for _ in range(2):
            self.rename('WP_R_4010', 'LATER NAME')
            self.store.catalog()
            self.store.record('weapon', 'WP_R_4010')
        self.assertEqual(self.store.defaults, snapshot)
        self.assertEqual(self.store.defaults, committed)
        self.assertNotIn('weapon:WP_R_4010', self.store.defaults)

    def test_user_override_wins_over_refreshed_automatic_name(self):
        self.store.catalog()
        self.store.update('weapon', 'WP_R_4010', {'names': {'en': 'My cannon'}})
        self.rename('WP_R_4010', 'MACHINEGUN 303B')
        self.assertEqual(self.name_en('weapon', 'WP_R_4010'), 'My cannon')

    def test_explicit_empty_override_stays_cleared_after_reimport(self):
        self.store.catalog()
        self.store.update('weapon', 'WP_R_4010', {'names': {'en': ''}})
        self.store.catalog()
        self.rename('WP_R_4010', 'MACHINEGUN 303B')
        self.assertIsNone(self.name_en('weapon', 'WP_R_4010'))

    def test_reset_returns_the_current_not_the_first_automatic_name(self):
        self.store.catalog()
        self.store.update('weapon', 'WP_R_4010', {'names': {'en': 'My cannon'}})
        self.rename('WP_R_4010', 'MACHINEGUN 303B')
        reset = self.store.update('weapon', 'WP_R_4010', {'reset': True})
        self.assertEqual(reset['names']['en'], 'MACHINEGUN 303B')
        self.assertEqual(reset['overridden_fields'], [])

    def test_committed_enemy_default_is_never_replaced_by_imported_name(self):
        self.rows.append({'kind': 'enemy', 'resource_id': 'C4200', 'name': 'BADGUY'})
        self.store.catalog()
        record = self.store.record('enemy', 'C4200')
        self.assertEqual(record['names']['en'], 'IB-07: SOL 644')

    def test_duplicate_variants_keep_first_row_name_deterministically(self):
        self.rows.append({'kind': 'weapon', 'resource_id': 'WP_R_4010',
                          'name': 'EARSHOT VARIANT'})
        record = self.store.record('weapon', 'WP_R_4010')
        self.assertEqual(record['names']['en'], 'EARSHOT')
        self.assertEqual(record['variants'], 2)


if __name__ == '__main__':
    unittest.main()
