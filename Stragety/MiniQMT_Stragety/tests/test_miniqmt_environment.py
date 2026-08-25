# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


STRATEGY_DIR = Path(__file__).resolve().parents[1]
if str(STRATEGY_DIR) not in sys.path:
    sys.path.insert(0, str(STRATEGY_DIR))

from core import config as cfg
from infra import connector


class MiniQmtEnvironmentTests(unittest.TestCase):
    def test_environment_path_override_takes_priority(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            userdata = Path(tmpdir) / 'userdata_mini'
            userdata.mkdir()
            with patch.dict(os.environ, {'MINIQMT_PATH': str(userdata)}):
                self.assertEqual(
                    os.path.normpath(str(userdata)),
                    cfg.resolve_miniqmt_path(),
                )

    def test_loads_xtquant_from_client_bundled_site_packages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            site_packages = Path(tmpdir) / 'site-packages'
            package = site_packages / 'xtquant'
            package.mkdir(parents=True)
            (package / '__init__.py').write_text(
                'BUNDLED_SDK = True\n', encoding='utf-8')

            original_module = sys.modules.pop('xtquant', None)
            original_path = list(sys.path)
            try:
                with patch.object(
                        cfg, 'XTQUANT_SITE_PACKAGES', str(site_packages)):
                    loaded = connector.load_xtquant()
                self.assertTrue(loaded.BUNDLED_SDK)
                self.assertEqual(
                    str(site_packages.resolve()), sys.path[0])
            finally:
                sys.modules.pop('xtquant', None)
                sys.path[:] = original_path
                if original_module is not None:
                    sys.modules['xtquant'] = original_module

    def test_market_connection_uses_supported_implicit_connection(self):
        xtquant = types.ModuleType('xtquant')
        xtquant.__path__ = []
        xtdata = types.ModuleType('xtquant.xtdata')
        calls = []
        xtdata.get_full_tick = lambda codes: calls.append(codes) or {}

        with patch.dict(sys.modules, {
                'xtquant': xtquant,
                'xtquant.xtdata': xtdata,
        }), patch.object(connector, '_log'):
            client = connector.MiniQMTConnector()
            self.assertTrue(client.connect_data())

        self.assertEqual([[cfg.STOCK_QMT]], calls)
        self.assertTrue(client.data_connected)


if __name__ == '__main__':
    unittest.main()
