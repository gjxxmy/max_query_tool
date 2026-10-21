"""Hosted-mode contract tests with synthetic credentials and mocked MAX data."""
import datetime as dt
import http.client
import io
import json
import threading
import unittest
from unittest.mock import patch

from cloud import COOKIE_NAME, CloudPreferences, make_cipher
from preferences import PreferencesError
import server


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.cipher = make_cipher("test-only-server-secret-" * 3)
        self.httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.cloud = True
        self.httpd.public_host = "max.example.test"
        self.httpd.cookie_cipher = self.cipher
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        server.REPORTS.clear()
        server.BUNDLES.clear()

    def close_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    def request(self, path, data=None, cookie="", **extra):
        headers = {"Host": self.httpd.public_host, "Origin": "https://" + self.httpd.public_host}
        if cookie:
            headers["Cookie"] = cookie.split(";", 1)[0]
        if data is not None:
            headers["Content-Type"] = "application/json"
        headers.update(extra)
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1])
        try:
            conn.request("GET" if data is None else "POST", path,
                         None if data is None else json.dumps(data), headers)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_saved_accounts_are_encrypted_private_and_survive_service_restart(self):
        status, headers, _ = self.request("/api/preferences")
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"]
        for flag in ("Secure", "HttpOnly", "SameSite=Lax", "Path=/"):
            self.assertIn(flag, cookie)
        key = "test-only-report-key-never-real"
        with patch.object(server.preferences, "save_profile", side_effect=AssertionError("Mac accessed")):
            status, headers, raw = self.request("/api/profiles", {"name": "测试账号", "api_key": key}, cookie)
        self.assertEqual(status, 200)
        state = json.loads(raw)
        cookie = headers["Set-Cookie"]
        self.assertNotIn(key, cookie)
        self.assertNotIn(key.encode(), raw)
        self.assertNotIn("keys", state)
        restarted = CloudPreferences(make_cipher("test-only-server-secret-" * 3), cookie.split(";", 1)[0])
        self.assertEqual(restarted.key_for(state["last_profile"]), key)
        _, _, other = self.request("/api/preferences")
        self.assertEqual(json.loads(other)["profiles"], [])
        _, _, same = self.request("/api/preferences", cookie=cookie)
        self.assertEqual(json.loads(same)["profiles"], state["profiles"])

    def test_tampered_expired_and_wrong_key_cookies_do_not_restore_credentials(self):
        prefs = CloudPreferences(self.cipher)
        state = prefs.save_profile("test", "test-only-key")
        raw_cookie = prefs.cookie().split(";", 1)[0]
        invalid = [raw_cookie[:-5] + "abcde", COOKIE_NAME + "=bad-token"]
        import time
        expired = self.cipher.encrypt_at_time(json.dumps(prefs.state).encode(), int(time.time()) - 366 * 86400)
        invalid.append(COOKIE_NAME + "=" + expired.decode())
        for value in invalid:
            self.assertEqual(CloudPreferences(self.cipher, value).public()["profiles"], [])
        wrong_key = make_cipher("different-test-server-secret-" * 3)
        self.assertEqual(CloudPreferences(wrong_key, raw_cookie).public()["profiles"], [])
        self.assertTrue(state["profiles"])

    def test_cookie_capacity_rejects_without_losing_existing_profiles(self):
        prefs = CloudPreferences(self.cipher)
        prefs.save_profile("retained", "test-only-key")
        before = prefs.public()
        with self.assertRaises(PreferencesError):
            prefs.save_profile("oversized", "x" * 4096)
        self.assertEqual(prefs.public(), before)

    def test_invalid_host_origin_and_cross_site_requests_are_rejected(self):
        for headers in ({"Host": "attacker.test"}, {"Origin": "https://attacker.test"},
                        {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request("/api/preferences", **headers)[0], 403)
        self.assertEqual(self.request("/api/profiles", {}, **{"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("/", **{"Sec-Fetch-Site": "cross-site",
                                             "Sec-Fetch-Mode": "navigate"})[0], 200)

    @patch("server.open_remote")
    def test_query_download_and_bundle_are_bound_to_creating_browser(self, remote):
        _, headers, _ = self.request("/api/preferences")
        cookie = headers["Set-Cookie"]
        _, headers, raw = self.request("/api/profiles", {"name": "saved", "api_key": "test-only-key"}, cookie)
        cookie = headers["Set-Cookie"]
        state = json.loads(raw)
        day = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=2)).isoformat()
        data = {"profile_id": state["last_profile"], "platform": "android",
                "identifier_type": "application", "identifier": "com.example.game",
                "date": day, "aggregated": True}
        remote.return_value = io.BytesIO(json.dumps({"status": 200,
            "ad_revenue_report_url": "https://storage.googleapis.com/test-only-report.csv"}).encode())
        status, headers, raw = self.request("/api/query", data, cookie)
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"]
        result = json.loads(raw)
        self.assertIn("api_key=test-only-key", remote.call_args.args[0])
        self.assertEqual(self.request(result["download_url"])[0], 400)
        payload = {"start_date": day, "end_date": day, "results": [result]}
        self.assertEqual(self.request("/api/bundle", payload)[0], 400)
        status, _, raw = self.request("/api/bundle", payload, cookie)
        self.assertEqual(status, 200)
        self.assertEqual(self.request(json.loads(raw)["download_url"])[0], 400)
        report = io.BytesIO(b"impressions,revenue\n1,0.01\n")
        report.headers = {}
        remote.return_value = report
        self.assertEqual(self.request(result["download_url"], cookie=cookie)[0], 200)


if __name__ == "__main__":
    unittest.main()
