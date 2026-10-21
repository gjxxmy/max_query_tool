#!/usr/bin/env python3
"""Local MAX report client. Keys can be saved in the macOS Keychain."""
import argparse
import contextvars
import datetime as dt
import http.server
import http.client
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from preferences import preferences, PreferencesError
from summary import prepare_summary

API = "https://r.applovin.com/max/userAdRevenueReport"
SUMMARY_API = "https://r.applovin.com/maxReport"
ROOT = Path(__file__).resolve().parent
REPORT_TTL = 2 * 60 * 60
REPORTS = {}
BUNDLES = {}
LOCK = threading.Lock()
REQUEST_PREFERENCES = contextvars.ContextVar("request_preferences", default=None)


def current_preferences():
    return REQUEST_PREFERENCES.get() or preferences


def current_owner():
    return getattr(current_preferences(), "owner", "")


def owns(record):
    return record.get("owner", "") == current_owner()


class ReportError(Exception):
    pass


def validate_identity(data):
    if not isinstance(data, dict):
        raise ReportError("请求格式不正确。")
    key = (current_preferences().key_for(data["profile_id"]) if data.get("profile_id") else
           str(data.get("api_key", "")).strip())
    if not key or len(key) > 4096 or any(c.isspace() for c in key):
        raise ReportError("请填写有效的 Report Key（不是 SDK Key）。")
    platform = data.get("platform")
    if platform not in ("android", "ios", "fireos"):
        raise ReportError("请选择 Android、iOS 或 Fire OS。")
    kind = data.get("identifier_type")
    if kind not in ("application", "store_id"):
        raise ReportError("请选择应用标识类型。")
    identifier = str(data.get("identifier", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", identifier):
        raise ReportError("请填写应用包名、Bundle ID 或商店 ID，不要填网址。")
    return {"api_key": key, "platform": platform, kind: identifier}


def validate_query(data):
    params = validate_identity(data)
    try:
        day = dt.date.fromisoformat(str(data.get("date", "")))
    except ValueError:
        raise ReportError("请选择有效日期。")
    today = dt.datetime.now(dt.timezone.utc).date()
    if not 0 <= (today - day).days <= 45:
        raise ReportError("日期须在过去 45 天内，且不能晚于今天（UTC）。")
    aggregated = data.get("aggregated")
    if not isinstance(aggregated, bool):
        raise ReportError("请选择报表类型。")
    return dict(params, date=day.isoformat(), aggregated=str(aggregated).lower())


def safe_report_url(url):
    if not isinstance(url, str):
        raise ReportError("接口未返回有效的报表下载地址。")
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower()
        valid_host = host == "storage.googleapis.com" or any(
            host.endswith("." + suffix) for suffix in
            ("amazonaws.com", "applovin.com", "cloudfront.net"))
        if (parsed.scheme != "https" or not valid_host or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.fragment):
            raise ValueError()
    except ValueError:
        raise ReportError("报表下载地址不在支持的官方存储域名内。")
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_report_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_remote(url):
    return urllib.request.build_opener(SafeRedirect()).open(
        urllib.request.Request(url, headers={"User-Agent": "Local-MAX-Report/1.0"}),
        timeout=60)


def remote_error(exc, download=False):
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return ("下载链接已过期或无权限，请重新查询。" if download else
                    "认证失败或无访问权限，请检查 Report Key 和应用归属。")
        if exc.code == 429:
            return "请求过于频繁，请稍后重试。"
        if exc.code == 404:
            return "未找到报表，请检查应用标识、平台和日期。"
        return f"AppLovin 服务返回 HTTP {exc.code}，请检查选项或稍后重试。"
    return "连接服务失败或超时，请检查网络后重试。"


def query_report(data):
    params = validate_query(data)
    try:
        with open_remote(API + "?" + urllib.parse.urlencode(params)) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ReportError("接口响应过大，请稍后重试。")
        result = json.loads(raw)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ReportError(remote_error(exc)) from None
    except (ValueError, UnicodeError):
        raise ReportError("接口未返回有效的 JSON，请稍后重试。") from None
    if not isinstance(result, dict):
        raise ReportError("接口响应格式不正确。")
    status = str(result.get("status", ""))
    if status in ("401", "403"):
        raise ReportError("认证失败或无访问权限，请检查 Report Key 和应用归属。")
    if status != "200" or not result.get("ad_revenue_report_url"):
        raise ReportError("报表尚不可用。请确认应用、日期和平台；UTC 日结束 8 小时后才提供数据。")
    url = safe_report_url(result["ad_revenue_report_url"])
    mode = "users" if params["aggregated"] == "true" else "impressions"
    identifier = params.get("application", params.get("store_id"))
    filename = f"MAX_{identifier}_{params['platform']}_{params['date']}_{mode}.csv"
    token = secrets.token_urlsafe(32)
    with LOCK:
        now = time.monotonic()
        for old in [k for k, v in REPORTS.items() if v["expires"] < now]:
            del REPORTS[old]
        if len(REPORTS) >= 100:
            del REPORTS[next(iter(REPORTS))]
        REPORTS[token] = {"url": url, "filename": filename, "expires": now + REPORT_TTL, "owner": current_owner(),
                         "date": params["date"], "platform": params["platform"],
                         "identifier": identifier, "aggregated": params["aggregated"]}
    # Remember the actual platform only after the official query succeeds.
    warning = ""
    try:
        current_preferences().remember_query(params, data.get("profile_id", ""))
    except PreferencesError:
        warning = "报表已就绪，但未能记住本次应用；请检查设置容量或保存权限。"
    return {"download_url": f"/download/{token}", "filename": filename,
            "date": params["date"], "platform": params["platform"],
            "identifier": identifier, "aggregated": params["aggregated"] == "true",
            "warning": warning}


def range_dates(start, end):
    try:
        start, end = dt.date.fromisoformat(str(start)), dt.date.fromisoformat(str(end))
    except ValueError:
        raise ReportError("请选择有效的开始和结束日期。") from None
    today = dt.datetime.now(dt.timezone.utc).date()
    if start > end:
        raise ReportError("开始日期不能晚于结束日期。")
    if start < today - dt.timedelta(days=45) or end > today:
        raise ReportError("所有日期须在过去 45 天窗口内，且不能晚于今天（UTC）。")
    if (end - start).days >= 45:
        raise ReportError("一次最多查询 45 天，请缩短日期范围。")
    return [(start + dt.timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def query_summary(data):
    identity = validate_identity(data)
    days = range_dates(data.get("start_date"), data.get("end_date"))
    kind = "application" if "application" in identity else "store_id"
    identifier = identity[kind]
    params = {"api_key": identity["api_key"], "start": days[0], "end": days[-1],
        "columns": "day,package_name,store_id,platform,ad_format,impressions,estimated_revenue",
        "filter_platform": identity["platform"],
        "filter_" + ("package_name" if kind == "application" else "store_id"): identifier,
        "format": "json", "not_zero": "1", "limit": "1000"}
    try:
        with open_remote(SUMMARY_API + "?" + urllib.parse.urlencode(params)) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ReportError("汇总响应过大，请缩短日期范围后重试。")
        upstream = json.loads(raw)
        if isinstance(upstream, dict) and str(upstream.get("code")) in ("401", "403"):
            raise ReportError("认证失败或无访问权限，请检查 Report Key 和应用归属。")
        result, csv_data = prepare_summary(upstream, days, identity)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ReportError(remote_error(exc)) from None
    except (ValueError, UnicodeError):
        raise ReportError("汇总数据不完整或格式异常，请稍后重试。") from None
    filename = f"MAX_{identifier}_{identity['platform']}_{days[0]}_to_{days[-1]}_summary.csv"
    token = secrets.token_urlsafe(32)
    with LOCK:
        now = time.monotonic()
        for old in [k for k, v in REPORTS.items() if v["expires"] < now]:
            del REPORTS[old]
        if len(REPORTS) >= 100:
            del REPORTS[next(iter(REPORTS))]
        REPORTS[token] = {"csv": csv_data, "filename": filename, "expires": now + REPORT_TTL, "owner": current_owner()}
    warning = ""
    try:
        if result["rows"]:
            current_preferences().remember_query(identity, data.get("profile_id", ""))
    except PreferencesError:
        warning = "汇总已就绪，但未能记住本次应用。"
    return dict(result, identifier=identifier, platform=identity["platform"],
        start_date=days[0], end_date=days[-1], filename=filename,
        download_url=f"/download/{token}", warning=warning,
        as_of=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))


def register_bundle(data):
    days = range_dates(data.get("start_date"), data.get("end_date"))
    results = data.get("results")
    if not isinstance(results, list) or len(results) != len(days):
        raise ReportError("逐日查询结果不完整，请重新查询。")
    records, identity = [], None
    with LOCK:
        now = time.monotonic()
        for day, item in zip(days, results):
            if not isinstance(item, dict) or item.get("date") != day:
                raise ReportError("逐日查询结果日期不一致，请重新查询。")
            link = item.get("download_url")
            if link:
                if not isinstance(link, str) or not re.fullmatch(r"/download/[A-Za-z0-9_-]{43}", link):
                    raise ReportError("下载记录无效，请重新查询。")
                report = REPORTS.get(link.rsplit("/", 1)[-1])
                if not report or not owns(report) or report["expires"] < now:
                    raise ReportError("部分下载记录已过期，请重新查询该日期范围。")
                if "url" not in report:
                    raise ReportError("汇总报表请直接下载 CSV，无需逐日打包。")
                if report["date"] != day:
                    raise ReportError("逐日查询结果日期不一致，请重新查询。")
                current = (report["identifier"], report["platform"], report["aggregated"])
                if identity and current != identity:
                    raise ReportError("无法将不同应用、平台或类型的报表打包。")
                identity = current
                records.append(dict(report))
            else:
                # Only the tool's own bounded message, never a signed upstream URL.
                records.append({"date": day, "error": "未获取报表，或查询已停止。"})
        if identity is None:
            raise ReportError("尚无可打包的报表。")
        identifier, platform, aggregated = identity
        mode = "users" if aggregated == "true" else "impressions"
        filename = f"MAX_{identifier}_{platform}_{days[0]}_to_{days[-1]}_{mode}.zip"
        for old in [key for key, bundle in BUNDLES.items() if bundle["expires"] < now]:
            del BUNDLES[old]
        if len(BUNDLES) >= 20:
            del BUNDLES[next(iter(BUNDLES))]
        token = secrets.token_urlsafe(32)
        BUNDLES[token] = {"records": records, "filename": filename,
                         "expires": now + REPORT_TTL, "owner": current_owner()}
    return {"download_url": f"/bundle/{token}", "filename": filename,
            "start_date": days[0], "end_date": days[-1],
            "identifier": identifier, "platform": platform,
            "aggregated": aggregated == "true",
            "ready_days": sum("url" in r for r in records), "total_days": len(days)}


def write_bundle(bundle, destination):
    """Stream a ZIP. Stage only one day at a time so failed CSVs are never included."""
    records = bundle["records"]
    notes = ["MAX 收入报表下载结果（日期为 UTC）", ""]
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr("查询说明.txt", "日期范围：" + records[0]["date"] + " 至 " + records[-1]["date"] +
            "\n每天一个原始 CSV，未合并或跨日汇总。\n部分日期可能查询或下载失败，详见下载结果.txt。\n")
        for record in records:
            if "url" not in record:
                notes.append(record["date"] + "：未下载（" + record["error"] + "）")
                continue
            # Small days stay in RAM; large days use an automatically deleted private temp file.
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as staged:
                try:
                    with open_remote(record["url"]) as response:
                        expected = response.headers.get("Content-Length", "")
                        count = 0
                        while True:
                            chunk = response.read(128 * 1024)
                            if not chunk:
                                break
                            staged.write(chunk)
                            count += len(chunk)
                        if expected.isdigit() and count != int(expected):
                            raise ReportError("文件传输不完整，请重新查询下载。")
                except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
                    notes.append(record["date"] + "：下载失败（" + remote_error(exc, download=True) + "）")
                    continue
                except ReportError as exc:
                    notes.append(record["date"] + "：下载失败（" + str(exc) + "）")
                    continue
                staged.seek(0)
                # Client disconnects propagate, so remaining days aren't fetched unnecessarily.
                with archive.open(record["filename"], "w", force_zip64=True) as entry:
                    shutil.copyfileobj(staged, entry, 128 * 1024)
            notes.append(record["date"] + "：已下载 " + record["filename"])
        archive.writestr("下载结果.txt", "\n".join(notes) + "\n")


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "LocalMAX/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(75)

    def handle_one_request(self):
        # Each request thread receives its own browser's credentials and owner.
        self.workspace = None
        token = REQUEST_PREFERENCES.set(None)
        try:
            super().handle_one_request()
        finally:
            REQUEST_PREFERENCES.reset(token)

    def log_message(self, *args):
        # Never print request URLs, keys, upstream errors or signed report links.
        pass

    def headers_for(self, status, content_type, length=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if getattr(self.server, "cloud", False):
            self.send_header("Strict-Transport-Security", "max-age=31536000")
            if self.workspace is not None and self.workspace.dirty:
                self.send_header("Set-Cookie", self.workspace.cookie())
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if length is not None:
            self.send_header("Content-Length", str(length))

    def send_json(self, status, value):
        raw = json.dumps(value, ensure_ascii=False).encode()
        self.headers_for(status, "application/json; charset=utf-8", len(raw))
        self.end_headers()
        self.wfile.write(raw)

    def local_request(self, mutation=False):
        port = self.server.server_address[1]
        cloud = getattr(self.server, "cloud", False)
        allowed = ({self.server.public_host} if cloud else {f"127.0.0.1:{port}", f"localhost:{port}"})
        if self.headers.get("Host") not in allowed:
            self.send_json(403, {"error": "访问域名不受支持。"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {("https://" if cloud else "http://") + host for host in allowed}:
            self.send_json(403, {"error": "请求来源不受支持，请从工具页面操作。"})
            return False
        public_navigation = (cloud and not mutation and self.command == "GET"
            and self.path == "/" and self.headers.get("Sec-Fetch-Mode") == "navigate")
        if self.headers.get("Sec-Fetch-Site") == "cross-site" and not public_navigation:
            self.send_json(403, {"error": "不允许跨站请求。"})
            return False
        if mutation and self.headers.get_content_type() != "application/json":
            self.send_json(415, {"error": "请求格式须为 JSON。"})
            return False
        if cloud:
            from cloud import CloudPreferences
            self.workspace = CloudPreferences(self.server.cookie_cipher, self.headers.get("Cookie", ""))
            REQUEST_PREFERENCES.set(self.workspace)
        return True

    def do_GET(self):
        if not self.local_request():
            return
        path = urllib.parse.urlsplit(self.path).path
        files = {"/": ("index.html", "text/html; charset=utf-8"),
                 "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                 "/style.css": ("style.css", "text/css; charset=utf-8")}
        if path in files:
            filename, content_type = files[path]
            raw = (ROOT / filename).read_bytes()
            self.headers_for(200, content_type, len(raw))
            self.end_headers()
            self.wfile.write(raw)
        elif path == "/health":
            self.send_json(200, {"ok": True, "app": "max-revenue-report"})
        elif path == "/api/preferences":
            try:
                self.send_json(200, current_preferences().public())
            except PreferencesError as exc:
                self.send_json(400, {"error": str(exc)})
        elif path.startswith("/download/"):
            self.download(path.removeprefix("/download/"))
        elif path.startswith("/bundle/"):
            self.download_bundle(path.removeprefix("/bundle/"))
        else:
            self.send_json(404, {"error": "页面不存在。"})

    def download(self, token):
        with LOCK:
            report = REPORTS.get(token)
        if not report or not owns(report) or report["expires"] < time.monotonic():
            self.send_download_error("下载记录已过期，请回到工具重新查询。")
            return
        if "csv" in report:
            self.headers_for(200, "text/csv; charset=utf-8", len(report["csv"]))
            self.send_header("Content-Disposition", f'attachment; filename="{report["filename"]}"')
            self.end_headers()
            self.wfile.write(report["csv"])
            return
        try:
            response = open_remote(report["url"])
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.send_download_error(remote_error(exc, download=True))
            return
        except ReportError as exc:
            self.send_download_error(str(exc))
            return
        try:
            with response:
                self.headers_for(200, "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{report["filename"]}"')
                size = response.headers.get("Content-Length", "")
                if size.isdigit():
                    self.send_header("Content-Length", size)
                self.end_headers()
                while True:
                    chunk = response.read(128 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (OSError, TimeoutError):
            # A partial transfer fails at browser level; do not log signed URLs.
            self.close_connection = True

    def download_bundle(self, token):
        with LOCK:
            bundle = BUNDLES.get(token)
        if not bundle or not owns(bundle) or bundle["expires"] < time.monotonic():
            self.send_download_error("打包记录已过期，请回到工具重新查询。")
            return
        self.headers_for(200, "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="{bundle["filename"]}"')
        self.end_headers()
        try:
            write_bundle(bundle, self.wfile)
        except (OSError, TimeoutError):
            self.close_connection = True

    def send_download_error(self, message):
        import html
        raw = ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
               "<title>下载未完成</title><link rel='stylesheet' href='/style.css'>"
               "<main class='download-error'><h1>下载未完成</h1><p>" + html.escape(message) +
               "</p><a href='/'>返回查询页面</a></main></html>").encode()
        self.headers_for(400, "text/html; charset=utf-8", len(raw))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if not self.local_request(mutation=True):
            return
        if self.path not in ("/api/query", "/api/summary", "/api/profiles", "/api/bundle"):
            self.send_json(404, {"error": "接口不存在。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384:
                raise ReportError("请求内容为空或过大。")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ReportError("请求格式不正确。")
            if self.path == "/api/profiles":
                result = current_preferences().save_profile(data.get("name"), data.get("api_key"))
            elif self.path == "/api/bundle":
                result = register_bundle(data)
            elif self.path == "/api/summary":
                result = query_summary(data)
            else:
                result = query_report(data)
            self.send_json(200, result)
        except (ValueError, UnicodeError):
            self.send_json(400, {"error": "请求格式不正确。"})
        except (ReportError, PreferencesError) as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception:
            self.send_json(500, {"error": "查询未完成，请稍后重试。"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cloud", action="store_true", help="Hosted HTTPS reverse-proxy mode")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    port = args.port or (int(os.environ.get("PORT", "10000")) if args.cloud else 8766)
    if args.cloud:
        public_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME") or os.environ.get("MAX_PUBLIC_HOST", "")
        if not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]+)?", public_host):
            raise SystemExit("Set RENDER_EXTERNAL_HOSTNAME or MAX_PUBLIC_HOST to the public HTTPS hostname.")
        from cloud import make_cipher
        cipher = make_cipher(os.environ.get("MAX_COOKIE_SECRET", ""))
    try:
        server = http.server.ThreadingHTTPServer(("0.0.0.0" if args.cloud else "127.0.0.1", port), Handler)
    except OSError:
        if args.cloud:
            raise
        # Double-clicking twice opens the existing instance, not another service.
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                ours = json.load(r).get("app") == "max-revenue-report"
        except Exception:
            ours = False
        if not ours:
            raise SystemExit(f"端口 {port} 已占用，可用 --port 指定其他端口。")
        if args.open:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{port}")
        return
    server.cloud = args.cloud
    if args.cloud:
        server.public_host = public_host
        server.cookie_cipher = cipher
    url = f"https://{public_host}" if args.cloud else f"http://127.0.0.1:{server.server_address[1]}"
    print(f"MAX 收入报表工具：{url}", flush=True)
    print("云端模式：各浏览器使用独立的加密账号 Cookie。" if args.cloud else
          "仅本机可访问。关闭此窗口或按 Control+C 停止；保存的 Report Key 存于 Mac 钥匙串。", flush=True)
    if args.open:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
