import hashlib
import io
import json
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, PropertyMock

from switcher import __version__
from switcher.config import ConfigError
from switcher.installer import Cancelled
from switcher.self_update import SelfUpdater, asset_url, release_version, select_asset, verify_executable


def executable():
    value = bytearray(512)
    value[:2] = b'MZ'
    struct.pack_into('<I', value, 60, 128)
    value[128:134] = b'PE\0\0\x64\x86'
    return bytes(value)


def asset(payload=None):
    payload = executable() if payload is None else payload
    return {'name': 'ChatGPT.Switch.exe', 'state': 'uploaded', 'size': len(payload),
            'browser_download_url': 'https://github.com/yiyinfaith/chatgpt-switch/releases/download/v9.0.0/ChatGPT.Switch.exe',
            'digest': 'sha256:' + hashlib.sha256(payload).hexdigest()}


class SelfUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = SelfUpdater(self.root, True)
        self.cancel = threading.Event()
        self.settings = {'mode': 'direct', 'address': ''}

    def response(self, payload, url='https://release-assets.githubusercontent.com/test'):
        response = io.BytesIO(payload)
        response.url = url
        return Mock(open=Mock(return_value=response))

    def test_semantic_comparison_and_reject_unstable_tags(self):
        self.assertGreater(release_version('v1.10.0'), release_version('1.9.9'))
        for value in ['latest', 'v2.0.0-beta', '2.0', '1.2.3.4']:
            with self.assertRaises(ConfigError):
                release_version(value)

    def test_asset_source_digest_and_ambiguity(self):
        self.assertEqual(select_asset({'assets': [asset()]})['name'], 'ChatGPT.Switch.exe')
        for items in [[], [asset(), asset()], [dict(asset(), digest=None)],
                      [dict(asset(), name='ChatGPT.Switch.arm64.exe')]]:
            with self.assertRaises(ConfigError):
                select_asset({'assets': items})
        for url in ['http://github.com/yiyinfaith/chatgpt-switch/releases/download/v1/a.exe',
                    'https://github.com.evil.test/yiyinfaith/chatgpt-switch/releases/download/v1/a.exe',
                    'https://github.com/other/repo/releases/download/v1/a.exe']:
            with self.assertRaises(ConfigError):
                asset_url(url)

    def test_pe_size_and_checksum_are_all_required(self):
        path = self.root / 'test.exe'
        path.write_bytes(executable())
        verify_executable(path, 512, asset()['digest'])
        for size, digest in [(511, asset()['digest']), (512, 'sha256:'+'0'*64)]:
            with self.assertRaises(ConfigError):
                verify_executable(path, size, digest)
        path.write_bytes(b'<html>' + b'x'*506)
        with self.assertRaises(ConfigError):
            verify_executable(path, 512, asset(path.read_bytes())['digest'])

    def test_check_newer_current_older_and_prerelease(self):
        for version, expected in [('v9.0.0', 'available'), ('v'+__version__, 'current'), ('v1.0.0', 'current')]:
            release = {'tag_name': version, 'assets': [asset()]}
            with patch.object(self.manager, 'opener', return_value=self.response(json.dumps(release).encode())):
                self.manager.check(self.settings, lambda _: None, self.cancel)
            self.assertEqual(self.manager.state['status'], expected)
            self.assertFalse(self.manager.state['canInstall'])  # Source/fixture mode must never replace Python.
        with patch.object(self.manager, 'opener', return_value=self.response(b'{"tag_name":"v9.0.0","prerelease":true}')):
            with self.assertRaises(ConfigError):
                self.manager.check(self.settings, lambda _: None, self.cancel)
        self.assertIsNone(self.manager.asset)

    def test_failed_check_clears_stale_download(self):
        self.manager.asset = asset()
        with patch.object(self.manager, 'opener', side_effect=OSError('offline')):
            with self.assertRaises(ConfigError):
                self.manager.check(self.settings, lambda _: None, self.cancel)
        self.assertIsNone(self.manager.asset)
        self.assertEqual(self.manager.state['status'], 'error')

    def test_download_cancellation_truncation_redirect_and_success(self):
        path = self.root / 'new.exe'
        with patch.object(self.manager, 'opener', return_value=self.response(executable())):
            self.manager.download(asset(), path, self.settings, lambda _: None, self.cancel)
        self.assertEqual(path.read_bytes(), executable())
        path.unlink()
        for payload, url in [(executable()[:100], 'https://github.com/file'),
                             (executable(), 'https://untrusted.test/file')]:
            with patch.object(self.manager, 'opener', return_value=self.response(payload, url)):
                with self.assertRaises(ConfigError):
                    self.manager.download(asset(), path, self.settings, lambda _: None, self.cancel)
            self.assertFalse(path.exists())
        self.cancel.set()
        with patch.object(self.manager, 'opener', return_value=self.response(executable())):
            with self.assertRaises(Cancelled):
                self.manager.download(asset(), path, self.settings, lambda _: None, self.cancel)
        self.assertFalse(path.exists())

    def test_source_mode_refuses_replacement(self):
        self.manager.asset = asset()
        self.manager.state['status'] = 'available'
        with self.assertRaises(ConfigError):
            self.manager.install(self.settings, lambda _: None, self.cancel)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_failed_handoff_keeps_old_executable_and_cleans_download(self):
        target = self.root / 'ChatGPT Switch.exe'
        target.write_bytes(b'original release')
        self.manager.asset = asset()
        self.manager.state.update(status='available', latest='v9.0.0')
        def download(_asset, path, *_args):
            path.write_bytes(executable())
        with patch.object(SelfUpdater, 'supported', new_callable=PropertyMock, return_value=True), \
             patch('switcher.self_update.sys.executable', str(target)), \
             patch.object(self.manager, 'download', side_effect=download), \
             patch.object(self.manager, 'handoff', side_effect=ConfigError('helper blocked')):
            with self.assertRaises(ConfigError):
                self.manager.install(self.settings, lambda _: None, self.cancel)
        self.assertEqual(target.read_bytes(), b'original release')
        self.assertEqual(list(self.root.iterdir()), [target])
        self.assertFalse(self.manager.pending)

    def test_proxy_scoped_to_opener(self):
        with patch('urllib.request.ProxyHandler') as handler, patch('urllib.request.build_opener'):
            for mode, address, expected in [('direct', '', {}), ('system', '', None),
                                            ('custom', 'http://127.0.0.1:7890', {'http':'http://127.0.0.1:7890','https':'http://127.0.0.1:7890'})]:
                self.manager.opener({'mode':mode,'address':address})
                handler.assert_called_with(expected)

    def test_update_routes_share_saved_proxy_and_current_page_override(self):
        from switcher.server import Application
        app = Application(self.root, self.root / 'fixture-config', test_mode=True)
        saved = {'mode':'custom', 'address':'http://127.0.0.1:7890'}
        app.action('/api/preferences', saved)
        for route, method in [('/api/app-update/check','check'), ('/api/app-update/install','install')]:
            for body, expected in [({}, saved), ({'proxyMode':'system','proxyAddress':''}, {'mode':'system','address':''}),
                                   ({'proxyMode':'direct','proxyAddress':''}, {'mode':'direct','address':''})]:
                with patch.object(app.self_updater, method, return_value={'message':'done'}) as operation:
                    app.action(route, body)
                    deadline = time.monotonic() + 3
                    while app.job['busy'] and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertFalse(app.job['busy'])
                    self.assertEqual(operation.call_args.args[0], expected)
        self.assertEqual(app.preferences(), saved)

    def test_background_check_uses_saved_proxy_without_taking_job_lock(self):
        from switcher.server import Application
        app = Application(self.root, self.root / 'fixture-config', test_mode=True)
        saved = {'mode': 'custom', 'address': 'http://127.0.0.1:7890'}
        app.action('/api/preferences', saved)
        job = dict(app.job)
        seen = []
        def check(probe, settings, progress, cancel):
            seen.append(settings)
            self.assertFalse(app.job['busy'])
            probe.state.update(status='available', latest='v9.0.0', canInstall=False)
            probe.asset = asset()
        with patch.object(SelfUpdater, 'check', check):
            self.assertEqual(app.check_update_in_background(), 6 * 60 * 60)
        self.assertEqual(seen, [saved])
        self.assertEqual(app.job, job)
        self.assertEqual(app.state()['appUpdate']['status'], 'available')
        self.assertEqual(app.self_updater.asset, asset())

    def test_background_network_failure_preserves_verified_update_and_retries(self):
        from switcher.server import Application
        app = Application(self.root, self.root / 'fixture-config', test_mode=True)
        app.self_updater.state.update(status='available', latest='v9.0.0', canInstall=True)
        app.self_updater.asset = asset()
        with patch.object(SelfUpdater, 'opener', side_effect=OSError('offline')):
            self.assertEqual(app.check_update_in_background(), 15 * 60)
        self.assertEqual(app.self_updater.state['status'], 'available')
        self.assertTrue(app.self_updater.state['canInstall'])
        self.assertEqual(app.self_updater.asset, asset())
        self.assertEqual(app.job['status'], 'idle')

    def test_background_result_cannot_overwrite_a_manual_check_or_install(self):
        from switcher.server import Application
        app = Application(self.root, self.root / 'fixture-config', test_mode=True)
        def check(probe, *_args):
            probe.state.update(status='current')
            app.action('/api/app-update/check', {})
            deadline = time.monotonic() + 3
            while app.job['busy'] and time.monotonic() < deadline:
                time.sleep(.01)
        with patch.object(SelfUpdater, 'check', check), patch.object(app.self_updater, 'check', return_value={'message': 'manual'}):
            self.assertEqual(app.check_update_in_background(), 60)
        self.assertEqual(app.self_updater.state['status'], 'unchecked')
        self.assertEqual(app.job['message'], 'manual')
        app.job.update(busy=True, kind='app-update')
        with patch.object(SelfUpdater, 'check') as check:
            self.assertEqual(app.check_update_in_background(), 60)
            check.assert_not_called()

    def test_background_check_does_not_publish_after_shutdown(self):
        from switcher.server import Application
        app = Application(self.root, self.root / 'fixture-config', test_mode=True)
        def check(probe, *_args):
            probe.state.update(status='available', latest='v9.0.0')
            app.update_check_stop.set()
        with patch.object(SelfUpdater, 'check', check):
            app.check_update_in_background()
        self.assertEqual(app.self_updater.state['status'], 'unchecked')

    def test_background_current_and_failed_checks_remain_quiet(self):
        from switcher.server import Application
        for payload, expected in [(json.dumps({'tag_name': 'v'+__version__}).encode(), 'current'), (b'invalid', 'error')]:
            app = Application(self.root, self.root / 'fixture-config', test_mode=True)
            with patch.object(SelfUpdater, 'opener', return_value=self.response(payload)):
                app.check_update_in_background()
            self.assertEqual(app.self_updater.state['status'], expected)
            self.assertEqual(app.job['status'], 'idle')
            self.assertFalse(app.self_updater.state['canInstall'])

    def test_background_scheduler_waits_then_rechecks_and_stops(self):
        from switcher.server import Application
        app = Application(self.root, self.root / 'fixture-config', test_mode=True)
        with patch.object(app.update_check_stop, 'wait', side_effect=[False, False, True]) as wait, \
             patch.object(app, 'check_update_in_background', side_effect=[900, 21600]) as check:
            app.start_update_checks()
            app.update_check_thread.join(timeout=2)
        self.assertFalse(app.update_check_thread.is_alive())
        self.assertEqual([c.args[0] for c in wait.call_args_list], [5, 900, 21600])
        self.assertEqual(check.call_count, 2)


if __name__ == '__main__':
    unittest.main()
