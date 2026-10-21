"""Validate MAX aggregate responses and calculate counts and USD totals exactly."""
import csv
import io
import re
from decimal import Decimal, InvalidOperation

FORMAT_NAMES = {"INTER": "插屏", "REWARD": "激励", "BANNER": "Banner",
                "MREC": "MREC", "NATIVE": "原生", "APPOPEN": "开屏", "UNKNOWN": "其他"}
MAX_SAFE_COUNT = 2 ** 53 - 1


def format_order(value):
    return ({"INTER": 0, "REWARD": 1, "BANNER": 2}.get(value, 3), value)


def metrics(count, revenue):
    if count > MAX_SAFE_COUNT:
        raise ValueError("count too large")
    ecpm = revenue * 1000 / count if count else None
    return {"impressions": count, "revenue": f"{revenue:.6f}",
            "ecpm": f"{ecpm:.6f}" if ecpm is not None else None}


def prepare_summary(result, days, identity):
    if not isinstance(result, dict) or str(result.get("code")) != "200":
        raise ValueError("invalid response")
    rows = result.get("results")
    # Refuse potentially truncated responses rather than showing partial totals.
    if (not isinstance(rows, list) or len(rows) >= 1000
            or str(result.get("count")) != str(len(rows))):
        raise ValueError("incomplete response")
    grouped = {}
    identifier_field = "package_name" if "application" in identity else "store_id"
    identifier = identity.get("application", identity.get("store_id"))
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid row")
        day, ad_format = row.get("day"), row.get("ad_format")
        if (day not in days or row.get("platform") != identity["platform"]
                or str(row.get(identifier_field)) != identifier
                or not isinstance(ad_format, str)
                or not re.fullmatch(r"[A-Z0-9_]{1,32}", ad_format)):
            raise ValueError("unexpected report scope")
        count = str(row.get("impressions"))
        if not re.fullmatch(r"[0-9]{1,16}", count):
            raise ValueError("invalid count")
        try:
            revenue = Decimal(str(row.get("estimated_revenue")))
            if not revenue.is_finite() or abs(revenue) > Decimal("1e18"):
                raise ValueError("invalid revenue")
        except InvalidOperation:
            raise ValueError("invalid revenue") from None
        if int(count) == 0 and revenue == 0:
            continue
        key = (day, ad_format)
        prior_count, prior_revenue = grouped.get(key, (0, Decimal(0)))
        grouped[key] = (prior_count + int(count), prior_revenue + revenue)
    daily, by_format = [], {}
    for (day, ad_format), (count, revenue) in sorted(
            grouped.items(), key=lambda item: (item[0][0], format_order(item[0][1]))):
        daily.append(dict(metrics(count, revenue), date=day, ad_format=ad_format))
        old_count, old_revenue = by_format.get(ad_format, (0, Decimal(0)))
        by_format[ad_format] = (old_count + count, old_revenue + revenue)
    totals = [dict(metrics(*by_format[key]), ad_format=key)
              for key in sorted(by_format, key=format_order)]
    total = metrics(sum(v[0] for v in by_format.values()),
                    sum((v[1] for v in by_format.values()), Decimal(0)))
    fullscreen = [by_format[key] for key in ("INTER", "REWARD") if key in by_format]
    fullscreen_total = metrics(sum(v[0] for v in fullscreen),
                              sum((v[1] for v in fullscreen), Decimal(0)))
    empty_days = [day for day in days if not any(row["date"] == day for row in daily)]
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["日期（UTC）", "广告类型", "展示次数", "预估收益（USD）", "eCPM（USD）"])
    for row in daily:
        writer.writerow([row["date"], FORMAT_NAMES.get(row["ad_format"], row["ad_format"]),
                         row["impressions"], row["revenue"], row["ecpm"] or ""])
    return {"rows": daily, "totals": totals, "total": total,
            "fullscreen_total": fullscreen_total, "empty_days": empty_days}, out.getvalue().encode("utf-8-sig")
