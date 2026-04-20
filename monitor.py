#!/usr/bin/env python3
import csv
import io
import json
import os
import smtplib
import sys
import time
import urllib.request
from datetime import datetime, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

STORE_URL = "https://www.yumecards.ca"
COLLECTION_PATH = "/collections/japanese-box"
PRICES_FILE = "data/prices.json"
HISTORY_FILE = "data/history.json"
DASHBOARD_FILE = "docs/index.html"
MAX_HISTORY = 1000


def fetch_all_products(base_url):
    products = []
    page = 1
    while True:
        url = f"{base_url}{COLLECTION_PATH}/products.json?limit=250&page={page}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            batch = data.get("products", [])
            if not batch:
                break
            products.extend(batch)
            print(f"  Page {page}: {len(batch)} products")
            page += 1
            time.sleep(1)
        except Exception as e:
            print(f"  Error on page {page}: {e}")
            break
    return products


def extract_prices(products):
    prices = {}
    for product in products:
        handle = product["handle"]
        title = product["title"]
        for variant in product.get("variants", []):
            key = f"{handle}::{variant['id']}"
            prices[key] = {
                "title": title,
                "variant": variant.get("title", "Default Title"),
                "price": variant["price"],
                "available": variant.get("available", False),
                "url": f"{STORE_URL}/products/{handle}",
            }
    return prices


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def detect_changes(old, new):
    changes = []
    now = datetime.now(timezone.utc).isoformat()

    for key, n in new.items():
        if key in old:
            o = old[key]
            if o["price"] != n["price"]:
                old_f = float(o["price"])
                new_f = float(n["price"])
                direction = "UP" if new_f > old_f else "DOWN"
                diff = abs(new_f - old_f)
                pct = (diff / old_f * 100) if old_f else 0
                changes.append({
                    "type": "price_change",
                    "direction": direction,
                    "title": n["title"],
                    "variant": n["variant"],
                    "old_price": o["price"],
                    "new_price": n["price"],
                    "diff": f"{diff:.2f}",
                    "pct": f"{pct:.1f}",
                    "url": n["url"],
                    "timestamp": now,
                })
        else:
            changes.append({
                "type": "new_product",
                "direction": "NEW",
                "title": n["title"],
                "variant": n["variant"],
                "old_price": None,
                "new_price": n["price"],
                "diff": n["price"],
                "pct": "N/A",
                "url": n["url"],
                "timestamp": now,
            })

    for key, o in old.items():
        if key not in new:
            changes.append({
                "type": "removed",
                "direction": "REMOVED",
                "title": o["title"],
                "variant": o["variant"],
                "old_price": o["price"],
                "new_price": None,
                "diff": "0",
                "pct": "N/A",
                "url": o["url"],
                "timestamp": now,
            })

    return changes


def build_email_html(changes):
    price_changes = [c for c in changes if c["type"] == "price_change"]
    new_products = [c for c in changes if c["type"] == "new_product"]
    removed = [c for c in changes if c["type"] == "removed"]

    rows = ""
    for c in price_changes:
        color = "#d4edda" if c["direction"] == "DOWN" else "#f8d7da"
        arrow = "&#9660;" if c["direction"] == "DOWN" else "&#9650;"
        rows += f"""
        <tr style="background:{color}">
          <td><a href="{c['url']}">{c['title']}</a></td>
          <td>{c['variant']}</td>
          <td>${c['old_price']} CAD</td>
          <td>${c['new_price']} CAD {arrow}</td>
          <td>{c['direction']} ${c['diff']} ({c['pct']}%)</td>
        </tr>"""

    table = f"""
    <table border="1" cellpadding="8" style="border-collapse:collapse;width:100%;font-family:Arial,sans-serif">
      <tr style="background:#222;color:white">
        <th>Product</th><th>Variant</th><th>Old Price</th><th>New Price</th><th>Change</th>
      </tr>
      {rows}
    </table>""" if price_changes else "<p>No direct price changes.</p>"

    extras = ""
    if new_products:
        items = ", ".join(f'<a href="{c["url"]}">{c["title"]}</a>' for c in new_products[:10])
        extras += f'<p><b>&#x1F195; New products added ({len(new_products)}):</b> {items}</p>'
    if removed:
        items = ", ".join(c["title"] for c in removed[:10])
        extras += f'<p><b>&#x274C; Products removed ({len(removed)}):</b> {items}</p>'

    return f"""<html><body style="font-family:Arial,sans-serif;max-width:900px;margin:auto">
    <h2 style="color:#333">&#128250; YumeCards Price Alert</h2>
    <p style="color:#666">{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC &mdash;
    {len(price_changes)} price change(s) detected on
    <a href="{STORE_URL}">yumecards.ca</a></p>
    {table}
    {extras}
    <hr>
    <small style="color:#999">Sent by PokéDash Price Monitor</small>
    </body></html>"""


def send_email(changes, gmail_user, gmail_password, recipient):
    msg = MIMEMultipart("alternative")
    price_changes = [c for c in changes if c["type"] == "price_change"]
    msg["Subject"] = f"[PokéDash] {len(price_changes)} price change(s) on YumeCards"
    msg["From"] = gmail_user
    msg["To"] = recipient
    msg.attach(MIMEText(build_email_html(changes), "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(gmail_user, gmail_password)
        smtp.sendmail(gmail_user, recipient, msg.as_string())
    print(f"  Email sent to {recipient}")


def build_csv(prices):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Product Name", "Variant", "Price (CAD)", "Availability", "Link"])
    for item in sorted(prices.values(), key=lambda x: x["title"]):
        variant = item["variant"] if item["variant"] != "Default Title" else ""
        availability = "In Stock" if item["available"] else "Out of Stock"
        writer.writerow([item["title"], variant, item["price"], availability, item["url"]])
    return output.getvalue().encode("utf-8")


def build_digest_html(prices):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    grouped = {}
    for item in prices.values():
        grouped.setdefault(item["title"], []).append(item)

    rows = ""
    for title in sorted(grouped.keys()):
        for v in grouped[title]:
            variant = v["variant"] if v["variant"] != "Default Title" else ""
            avail_color = "#155724" if v["available"] else "#721c24"
            avail = "In Stock" if v["available"] else "Out of Stock"
            rows += f"""<tr>
              <td><a href="{v['url']}" target="_blank">{title}</a></td>
              <td>{variant}</td>
              <td style="font-weight:bold">${v['price']} CAD</td>
              <td style="color:{avail_color}">{avail}</td>
            </tr>"""

    return f"""<html><body style="font-family:Arial,sans-serif;max-width:1000px;margin:auto">
    <h2 style="color:#333">&#128203; YumeCards Daily Price List</h2>
    <p style="color:#666">{now} &mdash; <b>{len(grouped)} products</b>, <b>{len(prices)} variants</b><br>
    The full price list is attached as an Excel-compatible CSV file.</p>
    <table border="1" cellpadding="8" style="border-collapse:collapse;width:100%;font-size:0.9em">
      <tr style="background:#222;color:white">
        <th>Product Name</th><th>Variant</th><th>Price (CAD)</th><th>Availability</th>
      </tr>
      {rows}
    </table>
    <hr><small style="color:#999">Sent by PokéDash Price Monitor &mdash; daily digest</small>
    </body></html>"""


def send_daily_digest(prices, gmail_user, gmail_password, recipient):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    grouped_count = len({v["title"] for v in prices.values()})

    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"[PokéDash] YumeCards Daily Price List — {today} ({grouped_count} products)"
    msg["From"] = gmail_user
    msg["To"] = recipient

    msg.attach(MIMEText(build_digest_html(prices), "html"))

    csv_data = build_csv(prices)
    attachment = MIMEBase("application", "octet-stream")
    attachment.set_payload(csv_data)
    encoders.encode_base64(attachment)
    attachment.add_header(
        "Content-Disposition",
        f"attachment; filename=yumecards_prices_{today}.csv"
    )
    msg.attach(attachment)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(gmail_user, gmail_password)
        smtp.sendmail(gmail_user, recipient, msg.as_string())
    print(f"  Daily digest sent to {recipient}")


def generate_dashboard(prices, history):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    recent = list(reversed(history[-200:]))

    rows = ""
    for c in recent:
        ts = c["timestamp"][:16].replace("T", " ")
        if c["type"] == "price_change":
            color = "#d4edda" if c["direction"] == "DOWN" else "#f8d7da"
            arrow = "▼" if c["direction"] == "DOWN" else "▲"
            rows += f"""<tr style="background:{color}">
              <td>{ts}</td>
              <td><a href="{c['url']}" target="_blank">{c['title']}</a></td>
              <td>{c['variant']}</td>
              <td>${c['old_price']}</td>
              <td>${c['new_price']} {arrow}</td>
              <td><b>{c['direction']}</b> ${c['diff']} ({c['pct']}%)</td>
            </tr>"""
        elif c["type"] == "new_product":
            rows += f"""<tr style="background:#fff3cd">
              <td>{ts}</td>
              <td><a href="{c['url']}" target="_blank">{c['title']}</a></td>
              <td>{c['variant']}</td>
              <td>—</td>
              <td>${c['new_price']}</td>
              <td><b>NEW</b></td>
            </tr>"""
        elif c["type"] == "removed":
            rows += f"""<tr style="background:#e2e3e5">
              <td>{ts}</td>
              <td>{c['title']}</td>
              <td>{c['variant']}</td>
              <td>${c['old_price']}</td>
              <td>—</td>
              <td><b>REMOVED</b></td>
            </tr>"""

    total_pc = len([c for c in history if c["type"] == "price_change"])
    recent_pc = len([c for c in history[-28:] if c["type"] == "price_change"])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PokéDash Price Monitor</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; background: #f0f2f5; color: #333; }}
  h1 {{ margin-bottom: 4px; }}
  .updated {{ color: #888; font-size: 0.85em; margin-bottom: 20px; }}
  .stats {{ display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 24px; }}
  .stat {{ background: white; padding: 16px 24px; border-radius: 10px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); min-width: 160px; }}
  .stat h3 {{ margin: 0; font-size: 2.2em; color: #e44; }}
  .stat p {{ margin: 4px 0 0; color: #666; font-size: 0.88em; }}
  .card {{ background: white; border-radius: 10px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); overflow: hidden; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.92em; }}
  th {{ background: #222; color: white; padding: 10px 12px; text-align: left; white-space: nowrap; }}
  td {{ padding: 8px 12px; border-top: 1px solid #eee; }}
  a {{ color: #0066cc; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .legend {{ font-size: 0.82em; color: #888; margin-top: 10px; }}
  .legend span {{ display: inline-block; width: 12px; height: 12px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }}
</style>
</head>
<body>
<h1>&#127921; PokéDash &mdash; YumeCards Price Monitor</h1>
<p class="updated">Last checked: <b>{now}</b> &nbsp;|&nbsp; Tracking <b>{len(prices):,}</b> product variants</p>

<div class="stats">
  <div class="stat"><h3>{total_pc}</h3><p>Total price changes logged</p></div>
  <div class="stat"><h3>{recent_pc}</h3><p>Changes (last 7 days)</p></div>
  <div class="stat"><h3>{len(prices):,}</h3><p>Variants tracked</p></div>
</div>

<div class="card">
  <table>
    <tr>
      <th>Time (UTC)</th>
      <th>Product</th>
      <th>Variant</th>
      <th>Old Price</th>
      <th>New Price</th>
      <th>Change</th>
    </tr>
    {rows if rows else '<tr><td colspan="6" style="text-align:center;padding:30px;color:#aaa">No changes detected yet. Check back after the first monitor run.</td></tr>'}
  </table>
</div>
<div class="legend">
  <span style="background:#d4edda"></span>Price dropped &nbsp;
  <span style="background:#f8d7da"></span>Price increased &nbsp;
  <span style="background:#fff3cd"></span>New product &nbsp;
  <span style="background:#e2e3e5"></span>Removed
</div>
</body>
</html>"""

    os.makedirs(os.path.dirname(DASHBOARD_FILE) or ".", exist_ok=True)
    with open(DASHBOARD_FILE, "w") as f:
        f.write(html)
    print(f"  Dashboard written to {DASHBOARD_FILE}")


def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting price check...")

    gmail_user = os.environ.get("GMAIL_USER", "")
    gmail_password = os.environ.get("GMAIL_PASSWORD", "")
    recipient = os.environ.get("ALERT_EMAIL", "pokedash.tcg@gmail.com")

    print(f"Fetching all products from {STORE_URL}...")
    products = fetch_all_products(STORE_URL)
    print(f"  Total products: {len(products)}")

    new_prices = extract_prices(products)
    print(f"  Total variants: {len(new_prices)}")

    old_prices = load_json(PRICES_FILE, {})
    history = load_json(HISTORY_FILE, [])

    if not old_prices:
        print("  First run — saving baseline prices. No alert sent.")
        save_json(PRICES_FILE, new_prices)
        save_json(HISTORY_FILE, history)
        generate_dashboard(new_prices, history)
        return

    changes = detect_changes(old_prices, new_prices)
    price_changes = [c for c in changes if c["type"] == "price_change"]
    print(f"  Changes: {len(changes)} total, {len(price_changes)} price changes")

    if changes:
        history.extend(changes)
        if len(history) > MAX_HISTORY:
            history = history[-MAX_HISTORY:]
        save_json(HISTORY_FILE, history)

    save_json(PRICES_FILE, new_prices)
    generate_dashboard(new_prices, history)

    if price_changes:
        if gmail_user and gmail_password:
            try:
                send_email(changes, gmail_user, gmail_password, recipient)
            except Exception as e:
                print(f"  Email error: {e}")
        else:
            print("  Gmail credentials not configured — skipping email.")
    else:
        print("  No price changes — no email sent.")

    print("Done.")


def daily_digest():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Sending daily digest...")

    gmail_user = os.environ.get("GMAIL_USER", "")
    gmail_password = os.environ.get("GMAIL_PASSWORD", "")
    recipient = os.environ.get("ALERT_EMAIL", "pokedash.tcg@gmail.com")

    prices = load_json(PRICES_FILE, {})
    if not prices:
        print("  No price data yet — run monitor first.")
        return

    if gmail_user and gmail_password:
        try:
            send_daily_digest(prices, gmail_user, gmail_password, recipient)
        except Exception as e:
            print(f"  Email error: {e}")
    else:
        print("  Gmail credentials not configured.")

    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--digest":
        daily_digest()
    else:
        main()
