"""Offline contract tests; never query AppLovin or use real credentials."""
import datetime as dt
import csv
import io
import json
import threading
import tempfile
from pathlib import Path
import unittest
import urllib.error
import urllib.request
import zipfile
from unittest.mock import patch

import server
from preferences import Preferences, PreferencesError


class MemoryVault:
    def __init__(self):
        self.keys = {}
    def save(self, account, key):
        self.keys[account] = key
    def read(self, account):
        return self.keys[account]


class Response(io.BytesIO):
    def __init__(self, raw, headers=None):
        super().__init__(raw)
        self.headers = headers or {}


class Tests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.vault = MemoryVault()
        self.preferences = Preferences(Path(self.directory.name), self.vault)
        patcher = patch('server.preferences', self.preferences)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.params = {"api_key": "test-only-not-a-real-key", "date":
            (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=2)).isoformat(),
            "platform": "android", "identifier_type": "application",
            "identifier": "com.example.game", "aggregated": False}
        server.REPORTS.clear()
        server.BUNDLES.clear()

    def summary_fixture(self):
        end = dt.date.fromisoformat(self.params['date'])
        start = (end - dt.timedelta(days=1)).isoformat()
        query = dict(self.params, start_date=start, end_date=end.isoformat())
        def row(day, kind, count, revenue):
            return {'day': day, 'ad_format': kind, 'impressions': str(count),
                'estimated_revenue': revenue, 'package_name': query['identifier'],
                'store_id': query['identifier'], 'platform': query['platform']}
        rows = [row(start, 'INTER', 100, '0.100001'),
                row(query['end_date'], 'INTER', 300, '0.200002'),
                row(query['end_date'], 'REWARD', 50, '0.050001'),
                row(query['end_date'], 'BANNER', 10, '0.001000')]
        return query, {'code': 200, 'count': len(rows), 'results': rows}

    @patch('server.open_remote')
    def test_summary_exact_totals_weighted_ecpm_and_saved_key(self, remote):
        query, upstream = self.summary_fixture()
        account = self.preferences.save_profile('汇总测试', self.params['api_key'])['last_profile']
        query.update(profile_id=account); query.pop('api_key')
        remote.return_value = Response(json.dumps(upstream).encode())
        result = server.query_summary(query)
        self.assertEqual(result['total'], {'impressions': 460, 'revenue': '0.351004', 'ecpm': '0.763052'})
        self.assertEqual(result['totals'][0], {'ad_format': 'INTER', 'impressions': 400,
            'revenue': '0.300003', 'ecpm': '0.750008'})
        self.assertEqual(result['fullscreen_total']['impressions'], 450)
        self.assertEqual(result['empty_days'], [])
        self.assertNotIn(self.params['api_key'], json.dumps(result))
        self.assertIn('/maxReport?', remote.call_args.args[0])
        self.assertIn('filter_package_name=com.example.game', remote.call_args.args[0])
        self.assertNotIn('filter_application=', remote.call_args.args[0])

    @patch('server.open_remote')
    def test_summary_store_id_empty_results_and_unknown_formats(self, remote):
        query, upstream = self.summary_fixture()
        query.update(identifier_type='store_id', identifier='123456789', platform='ios')
        remote.return_value = Response(json.dumps({'code': 200, 'count': 0, 'results': []}).encode())
        result = server.query_summary(query)
        self.assertEqual(result['total'], {'impressions': 0, 'revenue': '0.000000', 'ecpm': None})
        self.assertEqual(result['empty_days'], [query['start_date'], query['end_date']])
        self.assertNotIn(query['identifier'], self.preferences.public()['apps'])
        self.assertIn('filter_store_id=123456789', remote.call_args.args[0])
        row = dict(upstream['results'][0], store_id=query['identifier'], platform='ios', ad_format='NEW_FORMAT')
        remote.return_value = Response(json.dumps({'code': 200, 'count': 1, 'results': [row]}).encode())
        result = server.query_summary(query)
        self.assertEqual(result['totals'][0]['ad_format'], 'NEW_FORMAT')
        self.assertEqual(result['total']['impressions'], 100)

    @patch('server.open_remote')
    def test_summary_rejects_partial_wrong_scope_and_invalid_metrics(self, remote):
        query, upstream = self.summary_fixture()
        invalid = [dict(upstream, count=1000), {'code': 200}, {'code': 403}]
        for field, value in [('day', '2000-01-01'), ('platform', 'ios'),
                ('package_name', 'different.app'), ('impressions', '1.5'),
                ('impressions', '9007199254740992'), ('estimated_revenue', 'NaN'),
                ('ad_format', '=FORMULA')]:
            bad = dict(upstream['results'][0], **{field: value})
            invalid.append({'code': 200, 'count': 1, 'results': [bad]})
        for response in invalid:
            remote.return_value = Response(json.dumps(response).encode())
            with self.subTest(response=response), self.assertRaises(server.ReportError):
                server.query_summary(query)
        remote.side_effect = urllib.error.HTTPError('https://private/?api_key=secret', 403, 'private', {}, None)
        with self.assertRaises(server.ReportError) as caught:
            server.query_summary(query)
        self.assertNotIn('secret', str(caught.exception))

    @patch('server.open_remote')
    def test_http_summary_and_csv_download(self, remote):
        query, upstream = self.summary_fixture()
        remote.return_value = Response(json.dumps(upstream).encode())
        httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        base = f'http://127.0.0.1:{httpd.server_address[1]}'
        try:
            req = urllib.request.Request(base + '/api/summary', json.dumps(query).encode(),
                {'Content-Type': 'application/json', 'Origin': base})
            with urllib.request.urlopen(req) as response: result = json.load(response)
            with urllib.request.urlopen(base + result['download_url']) as response:
                raw = response.read()
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertIn('attachment', response.headers['Content-Disposition'])
            self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
            rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[0]['预估收益（USD）'], '0.100001')
            self.assertEqual(sum(int(r['展示次数']) for r in rows), result['total']['impressions'])
            self.assertNotIn(self.params['api_key'].encode(), raw)
            with self.assertRaises(server.ReportError):
                server.register_bundle({'start_date': query['start_date'], 'end_date': query['start_date'],
                    'results': [{'date': query['start_date'], 'download_url': result['download_url']}]})
        finally:
            httpd.shutdown(); httpd.server_close(); thread.join()

    def test_date_range_boundaries_and_inclusive_days(self):
        today = dt.datetime.now(dt.timezone.utc).date()
        self.assertEqual(server.range_dates(today - dt.timedelta(days=3), today - dt.timedelta(days=1)),
            [(today - dt.timedelta(days=i)).isoformat() for i in (3, 2, 1)])
        for start, end in [(today, today - dt.timedelta(days=1)),
                (today - dt.timedelta(days=46), today),
                (today - dt.timedelta(days=45), today),
                (today, today + dt.timedelta(days=1)), ('bad-date', today)]:
            with self.subTest(start=start, end=end), self.assertRaises(server.ReportError):
                server.range_dates(start, end)

    def ready_range(self, remote, count=2):
        end = dt.date.fromisoformat(self.params['date'])
        days = [(end - dt.timedelta(days=count - i - 1)).isoformat() for i in range(count)]
        results = []
        for day in days:
            remote.return_value = Response(json.dumps({'status': 200,
                'ad_revenue_report_url': f'https://storage.googleapis.com/report/{day}.csv'}).encode())
            results.append(server.query_report(dict(self.params, date=day)))
        return {'start_date': days[0], 'end_date': days[-1], 'results': results}

    @patch('server.open_remote')
    def test_http_zip_preserves_each_original_csv(self, remote):
        data = self.ready_range(remote)
        originals = [b'User ID,Revenue\r\none,0.10\r\n', b'User ID,Revenue\r\ntwo,0.20\r\n']
        remote.side_effect = [Response(raw, {'Content-Length': str(len(raw))}) for raw in originals]
        httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{httpd.server_address[1]}'
        try:
            request = urllib.request.Request(base + '/api/bundle', json.dumps(data).encode(),
                {'Content-Type': 'application/json', 'Origin': base})
            with urllib.request.urlopen(request) as response:
                result = json.load(response)
            self.assertEqual(result['ready_days'], 2)
            with urllib.request.urlopen(base + result['download_url']) as response:
                raw_zip = response.read()
                self.assertEqual(response.headers['Content-Type'], 'application/zip')
            with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
                self.assertIsNone(archive.testzip())
                for report, original in zip(data['results'], originals):
                    self.assertEqual(archive.read(report['filename']), original)
                self.assertIn('已下载', archive.read('下载结果.txt').decode())
                self.assertNotIn(self.params['api_key'].encode(), raw_zip)
        finally:
            httpd.shutdown(); httpd.server_close(); thread.join()

    @patch('server.open_remote')
    def test_partial_zip_omits_incomplete_csv_and_lists_missing_days(self, remote):
        data = self.ready_range(remote, 3)
        failed_name = data['results'][1]['filename']
        data['results'][2] = {'date': data['end_date'], 'error': 'Do not copy this private upstream detail'}
        result = server.register_bundle(data)
        self.assertEqual(result['ready_days'], 2)
        remote.side_effect = [Response(b'header\r\ncomplete\r\n'),
                              Response(b'incomplete', {'Content-Length': '999'})]
        out = io.BytesIO()
        server.write_bundle(server.BUNDLES[result['download_url'].rsplit('/', 1)[-1]], out)
        with zipfile.ZipFile(io.BytesIO(out.getvalue())) as archive:
            self.assertIsNone(archive.testzip())
            self.assertNotIn(failed_name, archive.namelist())
            self.assertEqual(sum(name.endswith('.csv') for name in archive.namelist()), 1)
            notes = archive.read('下载结果.txt').decode()
            self.assertIn('下载失败', notes)
            self.assertIn('未下载', notes)
            self.assertNotIn('private upstream', notes)

    @patch('server.open_remote')
    def test_bundle_rejects_arbitrary_stale_or_mixed_report_references(self, remote):
        data = self.ready_range(remote)
        changed = dict(data, results=[dict(data['results'][0], download_url='https://localhost/private'), data['results'][1]])
        with self.assertRaises(server.ReportError):
            server.register_bundle(changed)
        first_token = data['results'][0]['download_url'].rsplit('/', 1)[-1]
        server.REPORTS[first_token]['identifier'] = 'com.different.app'
        with self.assertRaises(server.ReportError):
            server.register_bundle(data)
        server.REPORTS[first_token]['identifier'] = 'com.example.game'
        server.REPORTS[first_token]['expires'] = 0
        with self.assertRaises(server.ReportError):
            server.register_bundle(data)

    def test_saved_profiles_persist_without_secret_on_disk(self):
        state = self.preferences.save_profile('常用账号', self.params['api_key'])
        account = state['last_profile']
        self.assertEqual(self.preferences.key_for(account), self.params['api_key'])
        reopened = Preferences(Path(self.directory.name), self.vault)
        self.assertEqual(reopened.public()['profiles'], state['profiles'])
        self.assertNotIn(self.params['api_key'], self.preferences.path.read_text())
        self.assertEqual(self.preferences.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(self.params['api_key'], json.dumps(state))
        with self.assertRaises(PreferencesError):
            self.preferences.key_for('not-a-valid-profile')
        with self.assertRaises(PreferencesError):
            self.preferences.save_profile('常用账号', 'duplicate')

    def test_application_platforms_remembered_without_guessing(self):
        params = server.validate_query(self.params)
        self.preferences.remember_query(params)
        self.assertEqual(self.preferences.public()['apps']['com.example.game'], ['android'])
        params['platform'] = 'ios'
        self.preferences.remember_query(params)
        state = self.preferences.public()
        self.assertEqual(state['apps']['com.example.game'], ['android', 'ios'])
        self.assertNotIn('com.unknown.game', state['apps'])

    @patch('server.open_remote')
    def test_query_uses_saved_key_without_key_in_request_body(self, remote):
        state = self.preferences.save_profile('离线测试账号', self.params['api_key'])
        params = dict(self.params, profile_id=state['last_profile'])
        params.pop('api_key')
        remote.return_value = Response(json.dumps({'status': 200,
            'ad_revenue_report_url': 'https://storage.googleapis.com/reports/test.csv'}).encode())
        result = server.query_report(params)
        self.assertNotIn(self.params['api_key'], json.dumps(result))
        self.assertIn(self.params['api_key'], remote.call_args.args[0])

    def test_http_preferences_save_list_and_query(self):
        httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{httpd.server_address[1]}'
        try:
            request = urllib.request.Request(base + '/api/profiles',
                json.dumps({'name': '测试账号', 'api_key': self.params['api_key']}).encode(),
                {'Content-Type': 'application/json', 'Origin': base})
            with urllib.request.urlopen(request) as response:
                state = json.load(response)
            self.assertEqual(state['profiles'][0]['name'], '测试账号')
            with urllib.request.urlopen(base + '/api/preferences') as response:
                listed = json.load(response)
            self.assertEqual(state, listed)
            self.assertNotIn(self.params['api_key'], json.dumps(listed))
        finally:
            httpd.shutdown(); httpd.server_close(); thread.join()

    def test_request_contract(self):
        params = server.validate_query(self.params)
        self.assertEqual(params["aggregated"], "false")
        self.assertNotIn("store_id", params)
        self.params.update(identifier_type="store_id", identifier="1207472156", platform="ios")
        params = server.validate_query(self.params)
        self.assertEqual(params["store_id"], "1207472156")
        self.assertNotIn("application", params)

    def test_invalid_inputs(self):
        for field, value in [("api_key", ""), ("platform", "other"),
                ("date", "2020-01-01"), ("date", "2100-01-01"),
                ("aggregated", "false"), ("identifier", "https://example.com"),
                ("identifier_type", "both")]:
            with self.subTest(field=field), self.assertRaises(server.ReportError):
                server.validate_query(dict(self.params, **{field: value}))

    def test_download_url_validation(self):
        server.safe_report_url("https://applovin-externalreports.s3.amazonaws.com/report.csv?a=b")
        server.safe_report_url("https://storage.googleapis.com/report-bucket/report.csv?a=b")
        for url in ["http://applovin-externalreports.s3.amazonaws.com/x", "https://127.0.0.1/x",
                "https://amazonaws.com.attacker.test/x", "https://a.amazonaws.com:80/x",
                "https://name:password@a.amazonaws.com/x", "file:///etc/passwd"]:
            with self.subTest(url=url), self.assertRaises(server.ReportError):
                server.safe_report_url(url)

    @patch("server.open_remote")
    def test_query_keeps_credentials_and_signed_url_off_frontend(self, remote):
        url = "https://applovin-externalreports.s3.amazonaws.com/report.csv?signature=test"
        remote.return_value = Response(json.dumps({"status": 200, "ad_revenue_report_url": url}).encode())
        result = server.query_report(self.params)
        body = json.dumps(result)
        self.assertNotIn(self.params["api_key"], body)
        self.assertNotIn("signature", body)
        self.assertTrue(result["filename"].endswith("impressions.csv"))
        request = remote.call_args.args[0]
        self.assertIn("aggregated=false", request)
        self.assertIn("application=com.example.game", request)

    @patch("server.open_remote")
    def test_errors_do_not_leak_upstream_details(self, remote):
        private = "https://example.com/?api_key=secret-marker"
        remote.side_effect = urllib.error.HTTPError(private, 403, private, {}, None)
        with self.assertRaises(server.ReportError) as caught:
            server.query_report(self.params)
        self.assertNotIn("secret-marker", str(caught.exception))
        self.assertIn("Report Key", str(caught.exception))
        remote.side_effect = None
        for response in [{"status": 204}, {"status": 200}, [], {"status": 401}]:
            remote.return_value = Response(json.dumps(response).encode())
            with self.assertRaises(server.ReportError):
                server.query_report(self.params)

    @patch("server.open_remote")
    def test_http_query_download_and_cross_origin_protection(self, remote):
        csv = b"User ID,Revenue\r\nexample-user,0.123456\r\n"
        remote.side_effect = [Response(json.dumps({"status": 200,
            "ad_revenue_report_url": "https://applovin-externalreports.s3.amazonaws.com/x"}).encode()),
            Response(csv, {"Content-Length": str(len(csv))})]
        httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            request = urllib.request.Request(base + "/api/query", json.dumps(self.params).encode(),
                {"Content-Type": "application/json", "Origin": base})
            with urllib.request.urlopen(request) as response:
                report = json.load(response)
            with urllib.request.urlopen(base + report["download_url"]) as response:
                self.assertEqual(response.read(), csv)
                self.assertIn("attachment", response.headers["Content-Disposition"])
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            bad = urllib.request.Request(base + "/api/query", b"{}",
                {"Content-Type": "application/json", "Origin": "https://unrelated.example"})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(bad)
            self.assertEqual(caught.exception.code, 403)
            token = report["download_url"].rsplit("/", 1)[-1]
            server.REPORTS[token]["expires"] = 0
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(base + report["download_url"])
            self.assertEqual(caught.exception.code, 400)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
