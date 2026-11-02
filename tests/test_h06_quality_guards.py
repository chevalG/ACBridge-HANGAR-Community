"""Narrow temp-data guards for the experimental mount and preset overlay.

These tests never read or write ``data/``, ``evidence/`` or the frozen job
cache.  They build a minimal ``AssetLibrary`` shell around a real
``JobManager`` inside ``TemporaryDirectory`` so the persisted-preset recovery
paths can be exercised without an engine, FLVER or GLB.

Tests marked ``expectedFailure`` reproduce defects recorded in
``docs/H06-QF-REVIEW.md``.  They assert the contract the API is expected to
keep; when the defect is fixed the decorator must be removed and the test
should pass unchanged.
"""
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hangar.errors import HangarError
from hangar.jobs import JobManager
from hangar.library import AssetLibrary

PARTS = {'HD': 'hd-part', 'BD': 'bd-part', 'AM': 'am-part', 'LG': 'lg-part'}


class StubCatalog:
    def snapshot(self):
        return {'parts': []}


class StubApp:
    def __init__(self, directory):
        self.data_dir = Path(directory)
        self.catalog_store = StubCatalog()
        self.jobs = JobManager(self.data_dir / 'jobs')


def make_library(app, presets):
    lib = AssetLibrary.__new__(AssetLibrary)
    lib.app = app
    lib.path = app.data_dir / 'library.json'
    lib.lock = threading.RLock()
    lib.mount_profiles = {}
    lib.npc_profiles = {}
    lib.converter_sha256 = 'test-converter'
    lib.data = {'schema_version': 1, 'roots': [], 'assets': [], 'counts': {},
                'assembly_presets': presets}
    return lib


def build_job(app, parts=PARTS):
    job = app.jobs.create('build', {'parts': dict(parts)})
    app.jobs.update(job['id'], status='completed')
    return job['id']


class PresetRecovery(unittest.TestCase):
    """Persisted presets must fail closed with a HangarError, never crash."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='H06 quality guards ')
        self.app = StubApp(self.tmp.name)
        self.build = build_job(self.app)

    def tearDown(self):
        self.app.jobs.shutdown(wait=True)
        self.tmp.cleanup()

    def recipe(self, selections):
        return {'id': 'preset', 'name': 'preset', 'parts': dict(PARTS),
                'weapon_selections': selections,
                'verification': 'parameter_dummy_technical'}

    def test_valid_preset_reaches_weapon_lookup(self):
        """A well-formed preset fails only because the stub library is empty."""
        lib = make_library(self.app, [self.recipe({'right_hand': {'asset_id': 'none',
                                                                  'mode': 'experimental'}})])
        with self.assertRaises(HangarError):
            lib.load_assembly({'preset_id': 'preset', 'build_job_id': self.build})

    def test_empty_selections_is_not_stop_iteration(self):
        """library.py:378 calls next(iter({})), which escapes as StopIteration/500."""
        lib = make_library(self.app, [self.recipe({})])
        with self.assertRaises(HangarError):
            lib.load_assembly({'preset_id': 'preset', 'build_job_id': self.build})

    def test_legacy_string_selection_is_normalised(self):
        """Pre-overlay library-mount jobs stored ``{slot: asset_id}`` strings.

        ``save_assembly`` accepts such a completed job, and ``_mount`` recovers
        the string form (library.py:388), but ``load_assembly`` indexes it as a
        mapping (library.py:378) and raises TypeError instead of a HangarError.
        """
        lib = make_library(self.app, [self.recipe({'right_hand': 'legacy-asset'})])
        with self.assertRaises(HangarError):
            lib.load_assembly({'preset_id': 'preset', 'build_job_id': self.build})


class SelectionContract(unittest.TestCase):
    """Adjustment objects are validated once here and again in mount_asset."""

    def setUp(self):
        self.lib = AssetLibrary.__new__(AssetLibrary)
        self.lib.mount_profiles = {}
        self.weapon = {'id': 'w', 'kind': 'weapon',
                       'resource_id': 'WP_L_0221', 'sha256': 'hash'}

    def test_invalid_adjustments_are_rejected(self):
        for adjustment in ({'translation': [float('nan'), 0, 0]},
                           {'translation': [float('inf'), 0, 0]},
                           {'translation': [0, 0, -float('inf')]},
                           {'translation': [100.0001, 0, 0]},
                           {'rotation': [0, 0, 360.0001]},
                           {'translation': [True, 0, 0]},
                           {'translation': [1, 2]},
                           {'translation': '0,0,0'},
                           {'rotation': [0, 0, 0, 0]},
                           ['invalid'], 'invalid', 1.5):
            with self.subTest(adjustment=adjustment), self.assertRaises(HangarError):
                self.lib._selection(self.weapon, 'left_hand', 'experimental', adjustment)

    def test_falsy_non_object_adjustment_is_rejected(self):
        """library.py:323 coerces falsy non-objects to ``{}`` before the type check."""
        for adjustment in ([], 0, False, ''):
            with self.subTest(adjustment=adjustment), self.assertRaises(HangarError):
                self.lib._selection(self.weapon, 'left_hand', 'experimental', adjustment)

    def test_unbounded_json_integer_is_rejected(self):
        """library.py:329 evaluates ``math.isfinite`` on an arbitrary-size int.

        JSON permits an integer of any length, so ``10**400`` reaches
        ``_selection`` and raises ``OverflowError`` before the ``abs`` limit is
        seen.  That escapes the API boundary as 500 ``internal_error`` instead
        of 400 ``invalid_adjustment``.
        """
        import json
        payload = json.loads('{"translation":[1' + '0' * 400 + ',0,0]}')
        with self.assertRaises(HangarError):
            self.lib._selection(self.weapon, 'left_hand', 'experimental', payload)

    def test_limits_are_inclusive_and_zero_is_normalised(self):
        selected = self.lib._selection(self.weapon, 'left_hand', 'experimental',
                                       {'translation': [100, -100, 0],
                                        'rotation': [360, -360, 0]})
        self.assertEqual(selected['mode'], 'experimental')
        self.assertEqual(selected['adjustment']['translation'], [100, -100, 0])
        self.assertEqual(selected['adjustment']['rotation'], [360, -360, 0])

    def test_verified_requires_proven_profile_and_zero_offset(self):
        self.lib.mount_profiles = {'WP_L_0221': {'source_sha256': 'hash',
                                                'slots': {'left_hand': 3023}}}
        selected = self.lib._selection(self.weapon, 'left_hand', 'verified')
        self.assertEqual(selected['mode'], 'verified')
        self.assertEqual(selected['adjustment'],
                         {'translation': [0, 0, 0], 'rotation': [0, 0, 0]})
        with self.assertRaises(HangarError):
            self.lib._selection(self.weapon, 'left_hand', 'verified',
                                {'translation': [0.001, 0, 0]})
        self.lib.mount_profiles = {}
        with self.assertRaises(HangarError):
            self.lib._selection(self.weapon, 'left_hand', 'verified')

    def test_mode_and_slot_are_closed_sets(self):
        with self.assertRaises(HangarError):
            self.lib._selection(self.weapon, 'left_hand', 'certified')
        with self.assertRaises(HangarError):
            self.lib._selection(self.weapon, 'left_rail', 'experimental')
        with self.assertRaises(HangarError):
            self.lib._selection({**self.weapon, 'kind': 'enemy'}, 'left_hand',
                                'experimental')


if __name__ == '__main__':
    unittest.main(verbosity=2)
