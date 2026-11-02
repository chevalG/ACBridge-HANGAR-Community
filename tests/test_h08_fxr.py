"""Bounded FXR provenance catalog: parser, closure, staging and catalog contracts."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest

from hangar import fxr_resources as fx


spec = importlib.util.spec_from_file_location(
    'h08_prepare', Path(__file__).resolve().parents[1] / 'tools/prepare_h08_fxr.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def make_fxr(effect_id, references=(), actions=()):
    """Minimal reviewed FXR5 layout: header, reference table, 64-byte actions."""
    refs = list(references)
    actions = [dict(action) for action in actions]
    ref_off = 0x78
    action_off = ref_off + 4 * len(refs)
    field_off = action_off + 64 * len(actions)
    total = field_off + 4 * sum(len(action.get('fields1', [])) + len(action.get('fields2', []))
                                for action in actions)
    blob = bytearray(max(total, 0x78))
    blob[0:4] = b'FXR\x00'
    struct.pack_into('<H', blob, 6, 5)
    struct.pack_into('<i', blob, 0x0C, effect_id)
    struct.pack_into('<2i', blob, 0x70, ref_off, len(refs))
    struct.pack_into('<2i', blob, 0x38, action_off, len(actions))
    for index, reference in enumerate(refs):
        struct.pack_into('<i', blob, ref_off + 4 * index, reference)
    cursor = field_off
    for index, action in enumerate(actions):
        offset = action_off + 64 * index
        fields1 = list(action.get('fields1', []))
        fields2 = list(action.get('fields2', []))
        struct.pack_into('<h', blob, offset, action['id'])
        struct.pack_into('<i', blob, offset + 8, len(fields1))
        struct.pack_into('<i', blob, offset + 20, len(fields2))
        struct.pack_into('<i', blob, offset + 32, cursor)
        for value in fields1 + fields2:
            struct.pack_into('<i', blob, cursor, value)
            cursor += 4
    return bytes(blob)


def make_tae(events):
    """Minimal TAE with one animation and event bodies for types 96 / 8000."""
    anims_off = 0x60
    body_off = anims_off + 16
    body_size = 40
    events_off = body_off + body_size
    data_base = events_off + 24 * len(events)
    params_base = data_base + 16 * len(events)
    floats_base = params_base + 32 * len(events)
    blob = bytearray(floats_base + 8 * len(events))
    blob[:4] = b'TAE '
    struct.pack_into('<i', blob, 0x54, 1)
    struct.pack_into('<q', blob, 0x58, anims_off)
    struct.pack_into('<q', blob, anims_off, 12345)
    struct.pack_into('<q', blob, anims_off + 8, body_off)
    struct.pack_into('<q', blob, body_off, events_off)
    struct.pack_into('<i', blob, body_off + 32, len(events))
    for index, event in enumerate(events):
        header = events_off + 24 * index
        data_off = data_base + 16 * index
        params_off = params_base + 32 * index
        start_off = floats_base + 8 * index
        end_off = start_off + 4
        struct.pack_into('<qqq', blob, header, start_off, end_off, data_off)
        struct.pack_into('<i', blob, data_off, event['type'])
        struct.pack_into('<q', blob, data_off + 8, params_off)
        struct.pack_into('<f', blob, start_off, event['start'])
        struct.pack_into('<f', blob, end_off, event['end'])
        if event['type'] == 96:
            struct.pack_into('<iii', blob, params_off,
                             event['ffx'], event['dummy'], event['slot'])
            blob[params_off + 12:params_off + 16] = bytes(event['flags'])
            struct.pack_into('<f', blob, params_off + 28, event['scale'])
        elif event['type'] == 8000:
            struct.pack_into('<iif', blob, params_off,
                             event['dummy'], event['ffx'], event['intensity'])
    return bytes(blob)


def make_dds(width=8, height=4, mips=0, fourcc=b'DX10', dxgi=98):
    blob = bytearray(132)
    blob[:4] = b'DDS '
    struct.pack_into('<2I', blob, 12, height, width)
    struct.pack_into('<I', blob, 28, mips)
    blob[84:88] = fourcc
    if fourcc == b'DX10':
        struct.pack_into('<I', blob, 128, dxgi)
    return bytes(blob)


PARAM_XML = """<param>
 <fields>
  <field name="a" type="u32" bitsize="-1" arraylength="1" defaultValue="5"/>
  <field name="b" type="u8" bitsize="4" arraylength="1" defaultValue="3"/>
  <field name="c" type="u8" bitsize="4" arraylength="1" defaultValue="2"/>
 </fields>
 <rows><row id="7" a="5" b="3" c="2"/></rows>
</param>"""


def make_param(value_a=5):
    blob = bytearray(0x60)
    struct.pack_into('<H', blob, 10, 1)
    struct.pack_into('<qQQ', blob, 0x40, 7, 0x58, 0)
    struct.pack_into('<I', blob, 0x58, value_a)
    blob[0x5C] = (2 << 4) | 3
    return bytes(blob)


def make_effect(ident=1, **overrides):
    effect = {
        'id': ident,
        'name': 'test effect',
        'category': 'muzzle',
        'source_path': 'Z:/not/on/this/machine/effect/f000000001.fxr',
        'source_sha256': 'a' * 64,
        'chains': [{'kind': 'tae_event'}],
        'references': [],
        'closure_ids': [ident],
        'resources': [],
        'warnings': [],
        'runtime_playback': 'not_implemented',
    }
    effect.update(overrides)
    return effect


def make_catalog(effects):
    return {'schema': fx.CATALOG_SCHEMA, 'effects': effects}


def write_catalog(data, catalog):
    data.mkdir(parents=True, exist_ok=True)
    (data / fx.CATALOG_NAME).write_text(json.dumps(catalog), encoding='utf-8')


def write_resource(root, relative, content=b'staged-bytes'):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def image_resource(relative, digest, **overrides):
    resource = {'name': 'plate.tif', 'type': 'image', 'relative': relative,
                'sha256': digest, 'source_path': 'Z:/source/plate.dds',
                'source_sha256': 'b' * 64}
    resource.update(overrides)
    return resource


class FxrParser(unittest.TestCase):
    def test_header_references_and_action_fields_are_read(self):
        blob = make_fxr(991501, references=[991500, 991502],
                        actions=[{'id': 132, 'fields1': [991502]},
                                 {'id': 7, 'fields2': [10, 20]}])
        parsed = fx.parse_fxr_bytes(blob, expected_id=991501)
        self.assertEqual(parsed['id'], 991501)
        self.assertEqual(parsed['version'], 5)
        self.assertEqual(parsed['bytes'], len(blob))
        self.assertEqual(parsed['sha256'], hashlib.sha256(blob).hexdigest())
        self.assertEqual(parsed['references'], [991500, 991502])
        self.assertEqual(parsed['action_count'], 2)
        self.assertEqual(parsed['action_ids'], [7, 132])
        self.assertEqual(parsed['actions'][0]['fields1'], [991502])
        self.assertEqual(parsed['actions'][1]['fields1'], [])
        self.assertEqual(parsed['actions'][1]['fields2'], [10, 20])
        self.assertEqual(parsed['action132_targets'], [991502])

    def test_magic_version_id_and_truncation_are_rejected(self):
        valid = make_fxr(90303)
        bad_magic = bytearray(valid)
        bad_magic[:4] = b'FXS\x00'
        with self.assertRaisesRegex(fx.FxrError, 'Not an FXR5 container'):
            fx.parse_fxr_bytes(bytes(bad_magic))
        bad_version = bytearray(valid)
        struct.pack_into('<H', bad_version, 6, 4)
        with self.assertRaisesRegex(fx.FxrError, 'version 5'):
            fx.parse_fxr_bytes(bytes(bad_version))
        with self.assertRaisesRegex(fx.FxrError, 'truncated'):
            fx.parse_fxr_bytes(valid[:0x70])
        with self.assertRaisesRegex(fx.FxrError, 'does not match'):
            fx.parse_fxr_bytes(valid, expected_id=90304)
        negative = bytearray(valid)
        struct.pack_into('<i', negative, 0x0C, 0)
        with self.assertRaisesRegex(fx.FxrError, 'must be positive'):
            fx.parse_fxr_bytes(bytes(negative))

    def test_tables_outside_the_blob_are_rejected(self):
        valid = bytearray(make_fxr(1, references=[2],
                                   actions=[{'id': 132, 'fields1': [2]}]))
        struct.pack_into('<i', valid, 0x38, 0x7FFFFFF0)
        with self.assertRaisesRegex(fx.FxrError, 'action table'):
            fx.parse_fxr_bytes(bytes(valid))
        bad_fields = bytearray(make_fxr(1, actions=[{'id': 132, 'fields1': [2]}]))
        struct.pack_into('<i', bad_fields, 0x78 + 32, 0x7FFFFFF0)
        with self.assertRaisesRegex(fx.FxrError, 'action fields'):
            fx.parse_fxr_bytes(bytes(bad_fields))

    def test_counts_are_bounded(self):
        blob = make_fxr(1, references=[2, 3], actions=[{'id': 132, 'fields1': [2, 3]}])
        with self.assertRaisesRegex(fx.FxrError, 'byte limit'):
            fx.parse_fxr_bytes(blob, limits={'max_fxr_bytes': 8})
        with self.assertRaisesRegex(fx.FxrError, 'reference count'):
            fx.parse_fxr_bytes(blob, limits={'max_references': 1})
        with self.assertRaisesRegex(fx.FxrError, 'action count'):
            fx.parse_fxr_bytes(blob, limits={'max_actions': 0})
        with self.assertRaisesRegex(fx.FxrError, 'too many fields'):
            fx.parse_fxr_bytes(blob, limits={'max_action_fields': 1})
        negative = bytearray(make_fxr(1, references=[2]))
        struct.pack_into('<i', negative, 0x74, -1)
        with self.assertRaisesRegex(fx.FxrError, 'reference count'):
            fx.parse_fxr_bytes(bytes(negative))

    def test_limited_reader_enforces_size_and_missing_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'x.fxr'
            path.write_bytes(b'x' * 32)
            with self.assertRaises(fx.FxrError) as raised:
                fx.read_limited_bytes(path, 8)
            self.assertEqual(raised.exception.code, 'fxr_limit')
            with self.assertRaises(fx.FxrError) as missing:
                fx.read_limited_bytes(Path(temporary) / 'absent.fxr', 8)
            self.assertEqual(missing.exception.status, 404)


class ReslistReader(unittest.TestCase):
    def test_bom_crlf_and_blank_lines(self):
        text = '\ufeffs10011_a.tif\r\n\r\n  s64002_n.tif  \r\n'
        self.assertEqual(fx.read_reslist_bytes(text.encode('utf-8')),
                         ['s10011_a.tif', 's64002_n.tif'])

    def test_separators_and_traversal_are_rejected(self):
        for entry in (b'a/b.tif', b'a\\b.tif', b'C:a.tif', b'a:b.tif',
                      b'..', b'.', b'a/../b.tif'):
            with self.subTest(entry=entry):
                with self.assertRaises(fx.FxrError) as raised:
                    fx.read_reslist_bytes(entry)
                self.assertEqual(raised.exception.code, 'fxr_structure')

    def test_line_and_entry_counts_are_bounded(self):
        with self.assertRaisesRegex(fx.FxrError, 'line exceeds'):
            fx.read_reslist_bytes(b'abcdef', limits={'max_reslist_line': 3})
        with self.assertRaisesRegex(fx.FxrError, 'entry count'):
            fx.read_reslist_bytes(b'a\r\nb\r\n', limits={'max_reslist_entries': 1})
        with self.assertRaisesRegex(fx.FxrError, 'UTF-8'):
            fx.read_reslist_bytes(b'\xff\xfe\x00')


class Closure(unittest.TestCase):
    def nodes(self):
        return {
            1: {'references': [2], 'action132_targets': [3],
                'direct_resource_dependencies': ['A.tif']},
            2: {'references': [1], 'action132_targets': [],
                'direct_resource_dependencies': ['a.tif', 'b.tif']},
            3: {'references': [], 'action132_targets': [1],
                'direct_resource_dependencies': ['c.tif']},
        }

    def test_reference_and_action132_edges_form_one_cycle(self):
        result = fx.closure(1, self.nodes())
        self.assertEqual(result['reachable'], [1, 2, 3])
        self.assertEqual(result['unresolved'], [])
        self.assertEqual(result['edges'], [[1, 2], [1, 3], [2, 1], [3, 1]])
        self.assertEqual(result['cycles'], [[1, 2, 3]])
        # Case-insensitive resource names collapse; output is deterministic.
        self.assertEqual(result['resources'], ['A.tif', 'b.tif', 'c.tif'])
        self.assertEqual(result, fx.closure(1, self.nodes()))

    def test_action132_only_edge_is_followed(self):
        nodes = {4: {'references': [], 'action132_targets': [5],
                     'direct_resource_dependencies': []},
                 5: {'references': [], 'action132_targets': [],
                     'direct_resource_dependencies': ['s31411_a.tif']}}
        result = fx.closure(4, nodes)
        self.assertEqual(result['reachable'], [4, 5])
        self.assertEqual(result['edges'], [[4, 5]])
        self.assertEqual(result['resources'], ['s31411_a.tif'])

    def test_missing_nodes_are_reported_not_invented(self):
        nodes = {1: {'references': [2], 'action132_targets': [],
                     'direct_resource_dependencies': []}}
        result = fx.closure(1, nodes)
        self.assertEqual(result['reachable'], [1])
        self.assertEqual(result['unresolved'], [2])
        self.assertEqual(result['edges'], [[1, 2]])
        self.assertEqual(result['cycles'], [])

    def test_self_loop_is_a_cycle(self):
        nodes = {3: {'references': [3], 'action132_targets': [],
                     'direct_resource_dependencies': []}}
        self.assertEqual(fx.closure(3, nodes)['cycles'], [[3]])

    def test_duplicate_and_invalid_edges(self):
        nodes = {1: {'references': [2, 2], 'action132_targets': [2],
                     'direct_resource_dependencies': []},
                 2: {'references': [], 'action132_targets': [],
                     'direct_resource_dependencies': []}}
        self.assertEqual(fx.closure(1, nodes)['edges'], [[1, 2]])
        invalid = {1: {'references': ['x'], 'action132_targets': [],
                       'direct_resource_dependencies': []}}
        with self.assertRaisesRegex(fx.FxrError, 'Closure reference'):
            fx.closure(1, invalid)
        bad_resource = {1: {'references': [], 'action132_targets': [],
                            'direct_resource_dependencies': ['']}}
        with self.assertRaisesRegex(fx.FxrError, 'invalid resource name'):
            fx.closure(1, bad_resource)

    def test_closure_traversal_is_bounded(self):
        nodes = {1: {'references': [2], 'action132_targets': [],
                     'direct_resource_dependencies': []},
                 2: {'references': [], 'action132_targets': [],
                     'direct_resource_dependencies': []}}
        with self.assertRaisesRegex(fx.FxrError, 'node count'):
            fx.closure(1, nodes, limits={'max_closure_nodes': 1})
        nodes[1]['references'] = [2, 3]
        nodes[3] = {'references': [], 'action132_targets': [],
                    'direct_resource_dependencies': []}
        with self.assertRaisesRegex(fx.FxrError, 'edge count'):
            fx.closure(1, nodes, limits={'max_closure_edges': 1})


class Catalog(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.data = self.root / 'data'
        self.resources = self.data / fx.RESOURCE_DIR
        self.digest = write_resource(self.resources, 'images/plate.png')

    def tearDown(self):
        self._temporary.cleanup()

    def catalog(self, **effect_overrides):
        return make_catalog([make_effect(resources=[
            image_resource('images/plate.png', self.digest, width=8, height=4),
            {'name': 'absent.tif', 'type': 'missing', 'relative': None, 'sha256': None,
             'source_path': None, 'source_sha256': None, 'reason': 'not staged'},
        ], **effect_overrides)])

    def test_catalog_loads_without_touching_recorded_source_paths(self):
        catalog = self.catalog()
        write_catalog(self.data, catalog)
        loaded = fx.load_catalog(self.data)
        self.assertEqual(loaded, catalog)
        rows = fx.local_resources(loaded, 1, self.data)
        self.assertTrue(rows[0]['exists'])
        self.assertFalse(rows[1]['exists'])
        self.assertTrue(fx.resource_file(loaded, 1, 'plate.tif', self.data).is_file())
        self.assertEqual(fx.effect(loaded, 1)['id'], 1)

    def test_tampered_resource_hash_is_rejected(self):
        write_catalog(self.data, self.catalog())
        (self.resources / 'images/plate.png').write_bytes(b'tampered')
        with self.assertRaisesRegex(fx.FxrError, 'hash does not match'):
            fx.load_catalog(self.data)
        self.assertEqual(fx.load_catalog(self.data, verify=False)['schema'], fx.CATALOG_SCHEMA)

    def test_schema_closure_and_runtime_playback_contracts(self):
        write_catalog(self.data, self.catalog())
        with self.assertRaisesRegex(fx.FxrError, 'Unsupported FX catalog schema'):
            fx.validate_catalog(make_catalog([]) | {'schema': 'other'}, self.data)
        with self.assertRaisesRegex(fx.FxrError, 'closure_ids must include'):
            fx.validate_catalog(self.catalog(closure_ids=[2]), self.data)
        with self.assertRaisesRegex(fx.FxrError, 'runtime_playback'):
            fx.validate_catalog(self.catalog(runtime_playback='play'), self.data)
        with self.assertRaisesRegex(fx.FxrError, 'Duplicate effect id'):
            fx.validate_catalog(make_catalog([make_effect(), make_effect()]), self.data)
        with self.assertRaisesRegex(fx.FxrError, 'effect count'):
            fx.validate_catalog(self.catalog(), self.data, limits={'max_effects': 0})

    def test_resource_paths_stay_inside_the_catalog_root(self):
        for relative in ('../plain.png', 'images/../../plain.png', '/abs/plain.png',
                         'C:/abs/plain.png', 'images\\plain.png', 'images//plain.png',
                         './plain.png', 'images/./plain.png'):
            with self.subTest(relative=relative):
                catalog = make_catalog([make_effect(resources=[
                    image_resource(relative, 'c' * 64)])])
                with self.assertRaises(fx.FxrError) as raised:
                    fx.validate_catalog(catalog, self.data)
                self.assertEqual(raised.exception.code, 'fxr_path')
        for relative in ('', 'plain.png'):
            catalog = make_catalog([make_effect(resources=[
                image_resource(relative, 'c' * 64)])])
            with self.assertRaises(fx.FxrError):
                fx.validate_catalog(catalog, self.data)

    def test_missing_resource_needs_a_reason_and_no_staged_file(self):
        missing = {'name': 'absent.tif', 'type': 'missing', 'relative': None,
                   'sha256': None, 'source_path': None, 'source_sha256': None,
                   'reason': 'not staged'}
        good = make_catalog([make_effect(resources=[missing])])
        fx.validate_catalog(good, self.data)
        for change in ({'reason': ''}, {'relative': 'images/plate.png'},
                       {'sha256': 'd' * 64}):
            bad = dict(missing) | change
            with self.subTest(change=change):
                with self.assertRaisesRegex(fx.FxrError, 'Missing resource'):
                    fx.validate_catalog(make_catalog([make_effect(resources=[bad])]), self.data)

    def test_image_suffix_and_dimension_bounds(self):
        bad_suffix = make_catalog([make_effect(resources=[
            image_resource('images/plate.jpg', self.digest)])])
        with self.assertRaisesRegex(fx.FxrError, '\\.png'):
            fx.validate_catalog(bad_suffix, self.data)
        too_wide = make_catalog([make_effect(resources=[
            image_resource('images/plate.png', self.digest, width=20000, height=4)])])
        with self.assertRaisesRegex(fx.FxrError, 'image bounds'):
            fx.validate_catalog(too_wide, self.data)
        too_many = make_catalog([make_effect(resources=[
            image_resource('images/plate.png', self.digest, width=8193, height=8192)])])
        with self.assertRaisesRegex(fx.FxrError, 'pixel count'):
            fx.validate_catalog(too_many, self.data)

    def test_stage_name_dedupes_bytes_and_suffixes_real_conflicts(self):
        taken = {}
        first, warning = fx.stage_name('images/a.png', 'a' * 64, taken)
        self.assertEqual((first, warning), ('images/a.png', None))
        again, warning = fx.stage_name('images/a.png', 'A' * 64, taken)
        self.assertEqual((again, warning), ('images/a.png', None))
        other, warning = fx.stage_name('images/a.png', 'b' * 64, taken)
        self.assertEqual(other, 'images/a-{}.png'.format('b' * 8))
        self.assertIn('different bytes', warning)
        third, _ = fx.stage_name('images/a.png', 'c' * 64, taken)
        self.assertNotEqual(third, other)
        self.assertTrue(third.endswith('.png'))


class PrepareHelpers(unittest.TestCase):
    def test_tae_event96_and_event8000_are_parsed_from_actual_offsets(self):
        blob = make_tae([
            {'type': 96, 'ffx': 991500, 'dummy': 800, 'slot': 1,
             'flags': [1, 2, 3, 4], 'scale': 1.25, 'start': 0.5, 'end': 3.5},
            {'type': 1, 'start': 0.0, 'end': 0.0},
            {'type': 8000, 'dummy': 3070, 'ffx': 90303, 'intensity': 2.5,
             'start': 0.0, 'end': 40000.0},
        ])
        parsed = prepare.parse_tae_bytes(blob)
        self.assertEqual(parsed['anim_count'], 1)
        self.assertEqual(parsed['sha256'], hashlib.sha256(blob).hexdigest())
        self.assertEqual(len(parsed['events']), 2)
        blade, thruster = parsed['events']
        self.assertEqual((blade['anim_id'], blade['event_type']), (12345, 96))
        self.assertEqual((blade['ffx_id'], blade['dummy_poly_id'], blade['slot_id']),
                         (991500, 800, 1))
        self.assertEqual(blade['flags'], [1, 2, 3, 4])
        self.assertAlmostEqual(blade['scale'], 1.25)
        self.assertEqual((blade['start'], blade['end'], blade['until_anim_end']),
                         (0.5, 3.5, False))
        self.assertEqual((thruster['event_type'], thruster['dummy_poly_id'],
                          thruster['ffx_id']), (8000, 3070, 90303))
        self.assertAlmostEqual(thruster['intensity'], 2.5)
        self.assertIsNone(thruster['end'])
        self.assertTrue(thruster['until_anim_end'])
        self.assertEqual(len(blade['params_hex']), 64)
        chain = prepare.tae_chains(parsed['events'], {'path': 'a.tae', 'sha256': 'e' * 64},
                                   'Event96')
        self.assertEqual(chain[0]['kind'], 'tae_event')
        self.assertEqual(chain[0]['slot_id'], 1)
        self.assertEqual(chain[1]['intensity'], 2.5)

    def test_tae_rejects_bad_container_and_truncation(self):
        with self.assertRaisesRegex(prepare.PreparationError, 'Not a TAE container'):
            prepare.parse_tae_bytes(b'TAX ' + b'\x00' * 0x60)
        with self.assertRaisesRegex(prepare.PreparationError, 'Not a TAE container'):
            prepare.parse_tae_bytes(b'TAE ')
        valid = bytearray(make_tae([]))
        struct.pack_into('<q', valid, 0x58, 0x7FFFFFFFFFFFFFF0)
        with self.assertRaisesRegex(prepare.PreparationError, 'animation table'):
            prepare.parse_tae_bytes(bytes(valid))

    def test_dds_header_reports_dxgi_and_fourcc(self):
        info = prepare.dds_header(make_dds(width=8, height=4, mips=0))
        self.assertEqual((info['width'], info['height'], info['mips']), (8, 4, 1))
        self.assertEqual((info['dxgi'], info['name']), (98, 'BC7_UNORM'))
        legacy = prepare.dds_header(make_dds(fourcc=b'DXT1'))
        self.assertIsNone(legacy['dxgi'])
        self.assertEqual(legacy['name'], 'DXT1')
        with self.assertRaisesRegex(prepare.PreparationError, 'Not a DDS texture'):
            prepare.dds_header(b'DDS ' + b'\x00' * 20)

    def test_param_layout_and_xml_crosscheck(self):
        import xml.etree.ElementTree as ET
        layout, width = prepare._param_layout(ET.fromstring(PARAM_XML))
        self.assertEqual(width, 5)
        self.assertEqual(layout['a']['offset'], 0)
        self.assertEqual(layout['b']['offset'], 4)
        self.assertEqual(layout['c']['offset'], 4)
        self.assertEqual(layout['b']['bit_offset'], 0)
        self.assertEqual(layout['c']['bit_offset'], 4)

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / 'tbl.param'
            base.write_bytes(make_param())
            base.with_name('tbl.param.xml').write_text(PARAM_XML, encoding='utf-8')
            table = prepare.read_param_table(base, {7: ['a', 'b', 'c']})
            fields = table['rows']['7']['fields']
            self.assertEqual([fields[key]['value'] for key in ('a', 'b', 'c')], [5, 3, 2])
            self.assertEqual(fields['a']['file_offset'], 0x58)
            self.assertTrue(all(row['matches_xml'] for row in fields.values()))

            base.write_bytes(make_param(value_a=6))
            with self.assertRaisesRegex(prepare.PreparationError, 'XML says'):
                prepare.read_param_table(base, {7: ['a']})
            base.write_bytes(make_param())
            with self.assertRaisesRegex(prepare.PreparationError, 'not present'):
                prepare.read_param_table(base, {8: ['a']})

    def test_param_schema_rejects_dtd_and_entities(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / 'tbl.param'
            base.write_bytes(make_param())
            base.with_name('tbl.param.xml').write_text(
                '<!DOCTYPE param [<!ENTITY x "boom">]>' + PARAM_XML, encoding='utf-8')
            with self.assertRaisesRegex(prepare.PreparationError, 'DTD or entities'):
                prepare.read_param_table(base, {7: ['a']})
            base.with_name('tbl.param.xml').write_text(
                '<param><fields><field name="a" type="u32" bitsize="-1" '
                'arraylength="1" defaultValue="5"/></fields><rows><row id="7" a="5"/>',
                encoding='utf-8')
            with self.assertRaisesRegex(prepare.PreparationError, 'not valid XML'):
                prepare.read_param_table(base, {7: ['a']})

    def test_safe_stem_rejects_non_plain_names(self):
        self.assertEqual(prepare.safe_stem('s81032.sib'), 's81032')
        with self.assertRaisesRegex(prepare.PreparationError, 'plain file stem'):
            prepare.safe_stem('')
        with self.assertRaisesRegex(prepare.PreparationError, 'plain file stem'):
            prepare.safe_stem('bad name.sib')


class StubJournal:
    def __init__(self):
        self.warnings = []

    def log(self, level, message):
        pass

    def warn(self, message):
        self.warnings.append(message)


class StubStager:
    def check(self, path, expected, role):
        return {'role': role, 'path': str(path),
                'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}

    def dependency(self, entry):
        return {'name': entry, 'type': 'missing', 'relative': None, 'sha256': None,
                'source_path': None, 'source_sha256': None, 'reason': 'stub'}


class GraphIntegration(unittest.TestCase):
    def test_load_graph_follows_action132_and_build_effect_unions_resources(self):
        with tempfile.TemporaryDirectory() as temporary:
            lib = Path(temporary) / 'lib'
            effects = lib / 'effect'
            reslist = lib / 'ResourceList'
            effects.mkdir(parents=True)
            reslist.mkdir(parents=True)
            (effects / 'f000000001.fxr').write_bytes(
                make_fxr(1, references=[2]))
            (effects / 'f000000002.fxr').write_bytes(
                make_fxr(2, actions=[{'id': 132, 'fields1': [3]}]))
            (effects / 'f000000003.fxr').write_bytes(make_fxr(3))
            for ident, entry in ((1, 'a.tif'), (2, 'b.tif'), (3, 's31411_a.tif')):
                (reslist / f'f{ident:09d}.ffxreslist').write_bytes(
                    (entry + '\r\n').encode('utf-8'))
            journal = StubJournal()
            nodes, missing = prepare.load_graph(lib, [1], StubStager(), journal)
            self.assertEqual(sorted(nodes), [1, 2, 3])
            self.assertEqual(missing, {})
            self.assertEqual(nodes[1]['references'], [2])
            self.assertEqual(nodes[2]['reference_list'], [])
            self.assertEqual(nodes[2]['action132_targets'], [3])
            self.assertEqual(nodes[2]['references'], [3])
            self.assertEqual(nodes[3]['references'], [])
            effect = prepare.build_effect(
                1, 'root', 'weapon_blade', nodes, missing, StubStager(), [],
                {'chains': [], 'tae_source': None})
            self.assertEqual(effect['closure_ids'], [1, 2, 3])
            self.assertEqual(effect['references'], [2])
            self.assertEqual([row['name'] for row in effect['resources']],
                             ['a.tif', 'b.tif', 's31411_a.tif'])
            self.assertEqual(effect['evidence']['closure']['edges'],
                             [[1, 2], [2, 3]])
            self.assertEqual(effect['runtime_playback'], 'not_implemented')

    def test_fxr_reference_chain_reports_both_edge_kinds(self):
        nodes = {1: {'path': 'p1', 'sha256': '1' * 64, 'reference_list': [2],
                     'action132_targets': [2]},
                 2: {'path': 'p2', 'sha256': '2' * 64}}
        chain = prepare.fxr_reference_chain(1, 2, nodes, {})
        self.assertEqual(chain['via'], ['ReferenceList', 'Action132'])
        self.assertTrue(chain['target_present'])
        missing = prepare.fxr_reference_chain(
            1, 3, {1: nodes[1]}, {3: {'path': 'p3'}})
        self.assertFalse(missing['target_present'])
        self.assertEqual(missing['target_path'], 'p3')


if __name__ == '__main__':
    unittest.main()
