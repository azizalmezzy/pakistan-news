#!/usr/bin/env python3
import http.server
import json
import urllib.request
import xml.etree.ElementTree as ET
import os
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
FAL_KEY       = os.environ.get("FAL_API_KEY", "")
DATABASE_URL  = os.environ.get("DATABASE_URL", "")

# ---- قاعدة البيانات ----
def get_db():
    try:
        import psycopg2
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"DB Error: {e}")
        return None

def init_db():
    db = get_db()
    if not db: return
    try:
        cur = db.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usage_log (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) NOT NULL,
                action VARCHAR(20) NOT NULL,
                cost FLOAT DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        db.commit()
        cur.close()
        db.close()
        print("✅ DB initialized")
    except Exception as e:
        print(f"DB init error: {e}")

def register_user(username):
    db = get_db()
    if not db: return False
    try:
        cur = db.cursor()
        cur.execute("INSERT INTO users (username) VALUES (%s) ON CONFLICT (username) DO NOTHING", (username,))
        db.commit()
        cur.close()
        db.close()
        return True
    except Exception as e:
        print(f"Register error: {e}")
        return False

def log_usage(username, action, cost):
    db = get_db()
    if not db: return
    try:
        cur = db.cursor()
        cur.execute("INSERT INTO usage_log (username, action, cost) VALUES (%s, %s, %s)", (username, action, cost))
        db.commit()
        cur.close()
        db.close()
    except Exception as e:
        print(f"Log error: {e}")

def get_all_stats():
    db = get_db()
    if not db: return []
    try:
        cur = db.cursor()
        cur.execute("""
            SELECT 
                username,
                SUM(CASE WHEN action='tweet' THEN 1 ELSE 0 END) as tweets,
                SUM(CASE WHEN action='image' THEN 1 ELSE 0 END) as images,
                SUM(cost) as total_cost,
                MAX(created_at) as last_active
            FROM usage_log
            GROUP BY username
            ORDER BY total_cost DESC
        """)
        rows = cur.fetchall()
        cur.close()
        db.close()
        return [{"username": r[0], "tweets": r[1], "images": r[2], 
                 "total_cost": round(r[3], 4), "last_active": str(r[4])} for r in rows]
    except Exception as e:
        print(f"Stats error: {e}")
        return []

def get_user_stats(username):
    db = get_db()
    if not db: return {}
    try:
        cur = db.cursor()
        import datetime
        now = datetime.datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # إحصائيات الشهر الحالي فقط
        cur.execute("""
            SELECT action, COUNT(*), SUM(cost)
            FROM usage_log WHERE username=%s AND created_at >= %s
            GROUP BY action
        """, (username, month_start))
        rows = cur.fetchall()

        # إجمالي كل الأوقات
        cur.execute("""
            SELECT SUM(cost) FROM usage_log WHERE username=%s
        """, (username,))
        total_all = cur.fetchone()[0] or 0

        # آخر 7 أيام
        cur.execute("""
            SELECT DATE(created_at), SUM(cost)
            FROM usage_log WHERE username=%s
            GROUP BY DATE(created_at)
            ORDER BY DATE(created_at) DESC LIMIT 7
        """, (username,))
        daily = cur.fetchall()

        # شهري — آخر 12 شهر
        cur.execute("""
            SELECT 
                TO_CHAR(DATE_TRUNC('month', created_at), 'YYYY-MM') as month,
                SUM(CASE WHEN action='tweet' THEN 1 ELSE 0 END) as tweets,
                SUM(CASE WHEN action='image' THEN 1 ELSE 0 END) as images,
                SUM(cost) as cost
            FROM usage_log WHERE username=%s
            GROUP BY DATE_TRUNC('month', created_at)
            ORDER BY DATE_TRUNC('month', created_at) ASC
            LIMIT 12
        """, (username,))
        monthly = cur.fetchall()

        cur.close()
        db.close()

        stats = {"tweets": 0, "images": 0, "month_cost": 0, "total_cost": round(total_all, 4), "daily": [], "monthly": []}
        for r in rows:
            if r[0] == 'tweet': stats["tweets"] = r[1]; stats["month_cost"] += r[2]
            if r[0] == 'image': stats["images"] = r[1]; stats["month_cost"] += r[2]
        stats["month_cost"] = round(stats["month_cost"], 4)
        stats["daily"] = [{"date": str(d[0]), "cost": round(d[1], 4)} for d in daily]
        stats["monthly"] = [{"month": m[0], "tweets": m[1], "images": m[2], "cost": round(m[3], 4)} for m in monthly]
        return stats
    except Exception as e:
        print(f"User stats error: {e}")
        return {}

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
    {"label": "باكستان الهند كشمير",    "url": "https://news.google.com/rss/search?q=%D8%A8%D8%A7%D9%83%D8%B3%D8%AA%D8%A7%D9%86+%D8%A7%D9%84%D9%87%D9%86%D8%AF+%D9%83%D8%B4%D9%85%D9%8A%D8%B1&hl=ar&gl=SA&ceid=SA:ar", "topic": "الهند"},
    # الهند
    {"label": "Pakistan India Border",      "url": "https://news.google.com/rss/search?q=Pakistan+India+border+tension+attack&hl=en-US&gl=US&ceid=US:en",          "topic": "الهند"},
    {"label": "Pakistan India Kashmir",     "url": "https://news.google.com/rss/search?q=Pakistan+India+Kashmir+conflict&hl=en-US&gl=US&ceid=US:en",               "topic": "الهند"},
    {"label": "India Pakistan Military",    "url": "https://news.google.com/rss/search?q=India+Pakistan+military+aggression+ceasefire&hl=en-US&gl=US&ceid=US:en", "topic": "الهند"},
    {"label": "India Pakistan Violation",   "url": "https://news.google.com/rss/search?q=India+Pakistan+LOC+violation+shelling&hl=en-US&gl=US&ceid=US:en",        "topic": "الهند"},
    # أفغانستان
    {"label": "Pakistan Afghanistan TTP",   "url": "https://news.google.com/rss/search?q=Pakistan+Afghanistan+TTP+attack&hl=en-US&gl=US&ceid=US:en",              "topic": "أفغانستان"},
    {"label": "Pakistan Taliban Border",    "url": "https://news.google.com/rss/search?q=Pakistan+Taliban+border+tension&hl=en-US&gl=US&ceid=US:en",              "topic": "أفغانستان"},
    {"label": "Pakistan Afghanistan Durand","url": "https://news.google.com/rss/search?q=Pakistan+Afghanistan+Durand+Line+clash&hl=en-US&gl=US&ceid=US:en",      "topic": "أفغانستان"},
    # أمن وتهريب
    {"label": "Pakistan Drug Smuggling",    "url": "https://news.google.com/rss/search?q=Pakistan+drug+smuggling+border+seizure&hl=en-US&gl=US&ceid=US:en",       "topic": "أمن وتهريب"},
    {"label": "Pakistan Arms Smuggling",    "url": "https://news.google.com/rss/search?q=Pakistan+arms+weapons+smuggling&hl=en-US&gl=US&ceid=US:en",              "topic": "أمن وتهريب"},
    {"label": "Pakistan Security Threat",   "url": "https://news.google.com/rss/search?q=Pakistan+security+threat+terrorism+attack&hl=en-US&gl=US&ceid=US:en",    "topic": "أمن وتهريب"},
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
        elif self.path == "/api/config":
            config = {"claude_key": ANTHROPIC_KEY, "fal_key": FAL_KEY}
            body = json.dumps(config).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        elif self.path.startswith("/api/register"):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            username = params.get("username", [""])[0].strip().lower()
            if username and len(username) >= 2:
                register_user(username)
                body = json.dumps({"ok": True, "username": username}).encode("utf-8")
            else:
                body = json.dumps({"ok": False, "error": "اسم قصير"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        elif self.path.startswith("/api/log"):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            username = params.get("username", [""])[0]
            action   = params.get("action", ["tweet"])[0]
            cost     = float(params.get("cost", [0])[0])
            if username:
                log_usage(username, action, cost)
            body = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        elif self.path.startswith("/api/stats/all"):
            stats = get_all_stats()
            body = json.dumps(stats, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        elif self.path.startswith("/api/stats/user"):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            username = params.get("username", [""])[0]
            stats = get_user_stats(username) if username else {}
            body = json.dumps(stats, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
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
    init_db()
    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"✅ السيرفر يعمل على port {PORT}")
    server.serve_forever()
