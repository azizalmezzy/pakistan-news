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

# ---- قوائم المصادر ----
WHITELIST = {
    # سعودية
    "spa.gov.sa", "aawsat.com", "asharq.com", "alarabiya.net",
    "arabnews.com", "okaz.com.sa", "alriyadh.com",
    # باكستانية
    "app.com.pk", "dawn.com", "thenews.com.pk", "tribune.com.pk", "brecorder.com",
    # وكالات دولية محايدة
    "reuters.com", "afp.com",
}

BLACKLIST = {
    # هندية
    "firstpost.com", "timesofindia.indiatimes.com", "economictimes.indiatimes.com",
    "ndtv.com", "theprint.in",
    # إيرانية
    "tasnimnews.com", "mehrnews.com", "presstv.ir", "irna.ir", "farsnews.ir",
}

def get_domain(url):
    """استخرج النطاق من الرابط"""
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower()
        return domain.replace("www.", "")
    except:
        return ""

def is_allowed(url, source=""):
    """هل المصدر مسموح به؟"""
    domain = get_domain(url)
    src = source.lower()
    # تحقق من القائمة السوداء أولاً
    for b in BLACKLIST:
        if b in domain or b in src:
            return False
    # إذا القائمة البيضاء فارغة اقبل الكل، وإلا تحقق منها
    for w in WHITELIST:
        if w in domain or w in src:
            return True
    # مصدر غير معروف — اقبله (لأن Google News يجيب مصادر كثيرة)
    return True

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
    # مدن باكستانية
    {"label": "Islamabad News",     "url": "https://news.google.com/rss/search?q=Islamabad+politics&hl=en-US&gl=US&ceid=US:en",                 "topic": "حكومة"},
    {"label": "Karachi News",       "url": "https://news.google.com/rss/search?q=Karachi+politics+Pakistan&hl=en-US&gl=US&ceid=US:en",          "topic": "حكومة"},
    {"label": "Lahore News",        "url": "https://news.google.com/rss/search?q=Lahore+politics+Pakistan&hl=en-US&gl=US&ceid=US:en",           "topic": "حكومة"},
    {"label": "Punjab Pakistan",    "url": "https://news.google.com/rss/search?q=Punjab+Pakistan+government&hl=en-US&gl=US&ceid=US:en",         "topic": "حكومة"},
    {"label": "Peshawar KPK",       "url": "https://news.google.com/rss/search?q=Peshawar+KPK+Pakistan+politics&hl=en-US&gl=US&ceid=US:en",     "topic": "حكومة"},
    {"label": "Rawalpindi News",    "url": "https://news.google.com/rss/search?q=Rawalpindi+Pakistan&hl=en-US&gl=US&ceid=US:en",                "topic": "جيش"},
    {"label": "Balochistan News",   "url": "https://news.google.com/rss/search?q=Balochistan+Pakistan+security&hl=en-US&gl=US&ceid=US:en",      "topic": "أمن وتهريب"},
    # حرب إيران
    {"label": "Pakistan Iran War",      "url": "https://news.google.com/rss/search?q=Pakistan+Iran+war+conflict&hl=en-US&gl=US&ceid=US:en",              "topic": "حرب إيران"},
    {"label": "Pakistan Iran Relations","url": "https://news.google.com/rss/search?q=Pakistan+Iran+relations+diplomacy&hl=en-US&gl=US&ceid=US:en",       "topic": "حرب إيران"},
    {"label": "Hormuz Strait Pakistan", "url": "https://news.google.com/rss/search?q=Hormuz+strait+Pakistan+oil+trade&hl=en-US&gl=US&ceid=US:en",        "topic": "حرب إيران"},
    {"label": "Iran US War Pakistan",   "url": "https://news.google.com/rss/search?q=Iran+US+war+Pakistan+mediator&hl=en-US&gl=US&ceid=US:en",           "topic": "حرب إيران"},
    {"label": "Iran Nuclear Pakistan",  "url": "https://news.google.com/rss/search?q=Iran+nuclear+Pakistan+region&hl=en-US&gl=US&ceid=US:en",            "topic": "حرب إيران"},
    {"label": "Middle East War Pakistan","url": "https://news.google.com/rss/search?q=Middle+East+war+Pakistan+impact&hl=en-US&gl=US&ceid=US:en",        "topic": "حرب إيران"},
    {"label": "باكستان إيران عربي",    "url": "https://news.google.com/rss/search?q=%D8%A8%D8%A7%D9%83%D8%B3%D8%AA%D8%A7%D9%86+%D8%A5%D9%8A%D8%B1%D8%A7%D9%86&hl=ar&gl=SA&ceid=SA:ar", "topic": "حرب إيران"},
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
                if not is_allowed(link, source):
                    continue
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

def translate_titles(articles):
    import json as _json
    import urllib.request as _req
    english = [(i, a) for i, a in enumerate(articles)
               if a.get('title') and not any(0x0600 <= ord(ch) <= 0x06FF for ch in a['title'])]
    if not english or not ANTHROPIC_KEY:
        return articles
    lines = []
    for i, a in english[:30]:
        lines.append(str(i) + ": " + a['title'])
    titles_text = "\n".join(lines)
    prompt = (
        "Translate these English news headlines to fluent Arabic. "
        "Keep the same format: number: translation. Only output the translations.\n\n"
        + titles_text
    )
    try:
        body = _json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": prompt}]
        }).encode("utf-8")
        request = _req.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01"
            },
            method="POST"
        )
        with _req.urlopen(request, timeout=20) as resp:
            data = _json.loads(resp.read())
        text = data.get("content", [{}])[0].get("text", "")
        for line in text.strip().split("\n"):
            if ":" in line:
                parts = line.split(":", 1)
                try:
                    article_idx = int(parts[0].strip())
                    translation = parts[1].strip()
                    if translation:
                        articles[article_idx]["titleAr"] = translation
                except (ValueError, IndexError):
                    pass
    except Exception as e:
        print(f"Translation error: {e}")
    return articles

def mark_top_articles(articles):
    """تصنيف الأخبار الأهم: مكررة في 3+ مصادر أو من مصادر tier-1"""
    import re
    
    TIER1 = {"reuters", "ap", "associated press", "bbc", "bloomberg", "al jazeera", 
             "the guardian", "new york times", "washington post", "ft", "financial times",
             "dawn", "geo news", "ary news", "the news"}
    
    def normalize(title):
        title = title.lower()
        title = re.sub(r"[^a-z0-9\u0600-\u06ff\s]", "", title)
        words = title.split()
        stopwords = {"the","a","an","in","on","at","to","for","of","and","or","is","are",
                     "was","were","has","have","pakistan","pakistani","says","said","will"}
        return set(w for w in words if w not in stopwords and len(w) > 3)
    
    def similarity(t1, t2):
        s1, s2 = normalize(t1), normalize(t2)
        if not s1 or not s2: return 0
        return len(s1 & s2) / max(len(s1 | s2), 1)
    
    # تجميع الأخبار المتشابهة
    groups = []
    used = set()
    for i, a in enumerate(articles):
        if i in used: continue
        group = [i]
        for j, b in enumerate(articles):
            if j <= i or j in used: continue
            if similarity(a.get("title",""), b.get("title","")) > 0.35:
                group.append(j)
                used.add(j)
        used.add(i)
        groups.append(group)
    
    # تصنيف الأهم
    top_indices = set()
    for group in groups:
        sources = {articles[i].get("source","").lower() for i in group}
        is_tier1 = any(any(t in s for t in TIER1) for s in sources)
        if len(group) >= 3 or is_tier1:
            # اختر الخبر الأقوى من المجموعة
            best = max(group, key=lambda i: (
                any(any(t in (articles[i].get("source","")).lower() for t in TIER1) for _ in [1]),
                len(articles[i].get("title",""))
            ))
            top_indices.add(best)
    
    for i, a in enumerate(articles):
        a["isTop"] = i in top_indices
    
    return articles

HTML_PAGE = open("index.html", "r", encoding="utf-8").read()

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/api/news":
            articles = fetch_all_news()
            articles = translate_titles(articles)
            articles = mark_top_articles(articles)
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

        elif self.path.startswith("/api/og-image"):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            url = params.get("url", [""])[0]
            image_url = ""
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Accept": "text/html,application/xhtml+xml"
                })
                with urllib.request.urlopen(req, timeout=8) as resp:
                    html = resp.read(50000).decode('utf-8', errors='ignore')
                # og:image
                import re
                og = re.search(r'<meta[^>]+property=(?:["\'])og:image(?:["\'])[^>]+content=(?:["\'])([^"\'>]+)(?:["\'])', html)
                if not og:
                    og = re.search(r'<meta[^>]+content=(?:["\'])([^"\'>]+)(?:["\'])[^>]+property=(?:["\'])og:image(?:["\'])', html)
                if not og:
                    og = re.search(r'<meta[^>]+name=(?:["\'])twitter:image(?:["\'])[^>]+content=(?:["\'])([^"\'>]+)(?:["\'])', html)
                if og:
                    image_url = og.group(1)
                    if image_url.startswith('//'):
                        image_url = 'https:' + image_url
            except Exception as e:
                print(f"OG image error: {e}")
            body = json.dumps({"image": image_url}).encode("utf-8")
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
