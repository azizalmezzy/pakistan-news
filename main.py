#!/usr/bin/env python3
import http.server
import json
import urllib.request
import xml.etree.ElementTree as ET
import os
import time
from concurrent.futures import ThreadPoolExecutor

PORT = int(os.environ.get("PORT", 8765))

FEEDS = [
    {"label": "Pakistan Politics",      "url": "https://news.google.com/rss/search?q=Pakistan+politics&hl=en-US&gl=US&ceid=US:en",                  "topic": "حكومة"},
    {"label": "Pakistan Government",    "url": "https://news.google.com/rss/search?q=Pakistan+government+prime+minister&hl=en-US&gl=US&ceid=US:en",  "topic": "حكومة"},
    {"label": "Pakistan Election",      "url": "https://news.google.com/rss/search?q=Pakistan+election&hl=en-US&gl=US&ceid=US:en",                   "topic": "انتخابات"},
    {"label": "Pakistan Diplomacy",     "url": "https://news.google.com/rss/search?q=Pakistan+diplomacy+foreign+policy&hl=en-US&gl=US&ceid=US:en",   "topic": "دبلوماسية"},
    {"label": "Pakistan India Kashmir", "url": "https://news.google.com/rss/search?q=Pakistan+India+Kashmir&hl=en-US&gl=US&ceid=US:en",              "topic": "علاقات دولية"},
    {"label": "Pakistan China CPEC",    "url": "https://news.google.com/rss/search?q=Pakistan+China+CPEC&hl=en-US&gl=US&ceid=US:en",                 "topic": "علاقات دولية"},
    {"label": "Pakistan US Relations",  "url": "https://news.google.com/rss/search?q=Pakistan+United+States+relations&hl=en-US&gl=US&ceid=US:en",    "topic": "علاقات دولية"},
    {"label": "Imran Khan PTI",         "url": "https://news.google.com/rss/search?q=Imran+Khan+PTI&hl=en-US&gl=US&ceid=US:en",                      "topic": "معارضة"},
    {"label": "Pakistan Army",          "url": "https://news.google.com/rss/search?q=Pakistan+army+military&hl=en-US&gl=US&ceid=US:en",              "topic": "جيش"},
    {"label": "Pakistan Parliament",    "url": "https://news.google.com/rss/search?q=Pakistan+parliament+assembly&hl=en-US&gl=US&ceid=US:en",        "topic": "برلمان"},
    {"label": "Pakistan Supreme Court", "url": "https://news.google.com/rss/search?q=Pakistan+supreme+court+judiciary&hl=en-US&gl=US&ceid=US:en",   "topic": "قضاء"},
    {"label": "Nawaz Sharif PMLN",      "url": "https://news.google.com/rss/search?q=Nawaz+Sharif+PMLN&hl=en-US&gl=US&ceid=US:en",                  "topic": "حكومة"},
    {"label": "باكستان سياسة",          "url": "https://news.google.com/rss/search?q=%D8%A8%D8%A7%D9%83%D8%B3%D8%AA%D8%A7%D9%86+%D8%B3%D9%8A%D8%A7%D8%B3%D8%A9&hl=ar&gl=SA&ceid=SA:ar", "topic": "حكومة"},
    {"label": "باكستان دبلوماسية",      "url": "https://news.google.com/rss/search?q=%D8%A8%D8%A7%D9%83%D8%B3%D8%AA%D8%A7%D9%86+%D8%AF%D8%A8%D9%84%D9%88%D9%85%D8%A7%D8%B3%D9%8A%D8%A9&hl=ar&gl=SA&ceid=SA:ar", "topic": "دبلوماسية"},
    {"label": "عمران خان",              "url": "https://news.google.com/rss/search?q=%D8%B9%D9%85%D8%B1%D8%A7%D9%86+%D8%AE%D8%A7%D9%86+%D8%A8%D8%A7%D9%83%D8%B3%D8%AA%D8%A7%D9%86&hl=ar&gl=SA&ceid=SA:ar", "topic": "معارضة"},
    {"label": "باكستان الهند كشمير",    "url": "https://news.google.com/rss/search?q=%D8%A8%D8%A7%D9%83%D8%B3%D8%AA%D8%A7%D9%86+%D8%A7%D9%84%D9%87%D9%86%D8%AF+%D9%83%D8%B4%D9%85%D9%8A%D8%B1&hl=ar&gl=SA&ceid=SA:ar", "topic": "علاقات دولية"},
]

def parse_date(d):
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(d).timestamp()
    except:
        return 0

def fetch_feed(feed):
    try:
        req = urllib.request.Request(
            feed["url"],
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)
        items = []
        for item in root.findall(".//item"):
            title  = item.findtext("title") or ""
            link   = item.findtext("link") or ""
            pub    = item.findtext("pubDate") or ""
            src_el = item.find("source")
            source = src_el.text if src_el is not None else "Google News"
            if " - " in title:
                title = title.rsplit(" - ", 1)[0].strip()
            if title:
                items.append({"title": title, "source": source, "url": link,
                              "publishedAt": pub, "topic": feed["topic"]})
        return items
    except Exception as e:
        print(f"  ✗ [{feed['label']}] {e}")
        return []

def fetch_all_news():
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(fetch_feed, FEEDS))
    all_items = [item for r in results for item in r]

    # آخر 7 أيام فقط
    seven_days_ago = time.time() - (7 * 24 * 60 * 60)
    all_items = [item for item in all_items if parse_date(item["publishedAt"]) >= seven_days_ago]

    all_items.sort(key=lambda x: parse_date(x["publishedAt"]), reverse=True)

    # إزالة المكرر
    seen = set()
    unique = []
    for item in all_items:
        key = item["title"][:60].lower().replace(" ", "")
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    print(f"✅ {len(unique)} خبر من آخر 7 أيام")
    return unique

HTML_PAGE = open("index.html", "r", encoding="utf-8").read()

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/api/news":
            articles = fetch_all_news()
            body = json.dumps({"articles": articles}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ("/", "/index.html"):
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"✅ السيرفر يعمل على port {PORT}")
    server.serve_forever()
