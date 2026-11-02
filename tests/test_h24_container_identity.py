import unittest
from hangar.dynamic_catalog import character


class ContainerIdentity(unittest.TestCase):
    def test_known_unpacked_and_divided_containers_only(self):
        for folder in ('c4200', 'c4200_div00', 'c4200_div01-anibnd-dcx-wanibnd',
                       'c4200-anibnd-dcx-wanibnd'):
            self.assertEqual(character('root/' + folder + '/hkx/a000_000001.hkx'), 'C4200')
        self.assertEqual(character('root/WP_L_0411-anibnd-wanibnd/hkx/a000_040000.hkx'), 'WP_L_0411')
        for folder in ('c4200-maybe', 'c4200_backup', 'c4200_divxx', 'c4200_div00-maybe'):
            self.assertIsNone(character('root/' + folder + '/a000_000001.hkx'))


if __name__ == '__main__':
    unittest.main()
