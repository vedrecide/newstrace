import os, re, json, time, csv, random, logging, threading, base64, io
import pandas as pd
from collections import Counter, defaultdict
from datetime import datetime
from urllib.parse import urljoin, urlparse, parse_qs, urlencode

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import matplotlib.pyplot as plt
import networkx as nx

# NLP Libraries (optional)
try:
    import spacy
    from spacy.lang.en.stop_words import STOP_WORDS
    nlp = spacy.load("en_core_web_sm")
    NLP_AVAILABLE = True
except:
    NLP_AVAILABLE = False
    nlp = None

logger = logging.getLogger(__name__)

# Thread-safe storage per domain
domain_data = defaultdict(lambda: {
    'seen_pairs': set(),
    'lock': threading.Lock(),
    'count': 0
})

# Expanded user agent rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
]

def extract_domain(url):
    try:
        domain = urlparse(url).netloc.lower()
        domain = domain.replace('www.', '')
        return domain
    except:
        return "unknown"

def sanitize_filename(domain):
    """Convert domain to valid filename"""
    sanitized = re.sub(r'[^\w\-.]', '_', domain)
    return sanitized

def extract_keywords_fallback(text):
    """Fallback keyword extraction"""
    if not text:
        return []
    stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 
                  'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'been', 'be',
                  'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
                  'should', 'may', 'might', 'must', 'can', 'this', 'that', 'these', 'those'}
    words = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b|\b[a-z]{4,}\b', text)
    filtered_words = [w.title() for w in words if w.lower() not in   stop_words and len(w) > 3]
    word_freq = Counter(filtered_words)
    return [word for word, _ in word_freq.most_common(10)]

def extract_keywords_nlp(text):
    """Extract keywords using spaCy if available, otherwise fallback"""
    if not NLP_AVAILABLE or not text:
        return extract_keywords_fallback(text)
    try:
        doc = nlp(text.lower())
        keywords = []
        for ent in doc.ents:
            if ent.label_ in ['PERSON', 'ORG', 'GPE', 'LOC', 'EVENT', 'PRODUCT', 'NORP']:
                keywords.append(ent.text.title())
        for chunk in doc.noun_chunks:
            chunk_text = chunk.text.strip()
            if (2 <= len(chunk_text.split()) <= 4 and 
                not all(token.is_stop for token in chunk)):
                keywords.append(chunk_text.title())
        for token in doc:
            if ((token.pos_ in ['NOUN', 'PROPN']) and 
                not token.is_stop and 
                len(token.text) > 3 and
                token.text.isalpha()):
                keywords.append(token.text.title())
        seen = set()
        unique_keywords = []
        for kw in keywords:
            kw_lower = kw.lower()
            if kw_lower not in seen and len(unique_keywords) < 10:
                seen.add(kw_lower)
                unique_keywords.append(kw)
        return unique_keywords if unique_keywords else extract_keywords_fallback(text)
    except Exception as e:
        logger.warning(f"NLP extraction failed: {e}, using fallback")
        return extract_keywords_fallback(text)

def extract_topics(text, keywords):
    """Extract topics from text"""
    topics = set()
    topic_keywords = {
        'Politics': ['election', 'government', 'minister', 'parliament', 'policy', 'vote', 'president', 'congress', 'senate', 'political'],
        'Economy': ['economy', 'market', 'business', 'finance', 'stock', 'trade', 'gdp', 'inflation', 'bank', 'economic'],
        'Technology': ['technology', 'tech', 'ai', 'digital', 'software', 'internet', 'cyber', 'app', 'innovation', 'startup'],
        'Health': ['health', 'medical', 'hospital', 'disease', 'treatment', 'doctor', 'patient', 'medicine', 'covid', 'vaccine'],
        'Sports': ['sport', 'cricket', 'football', 'match', 'player', 'team', 'championship', 'olympic', 'tournament', 'game'],
        'Entertainment': ['entertainment', 'movie', 'film', 'actor', 'music', 'celebrity', 'show', 'series', 'bollywood', 'hollywood'],
        'Environment': ['climate', 'environment', 'pollution', 'green', 'sustainability', 'energy', 'renewable', 'carbon', 'nature'],
        'Crime': ['crime', 'police', 'arrest', 'court', 'murder', 'theft', 'investigation', 'law', 'justice', 'trial'],
        'Education': ['education', 'school', 'university', 'student', 'exam', 'teacher', 'learning', 'college', 'academic'],
        'International': ['international', 'world', 'global', 'foreign', 'country', 'nation', 'embassy', 'diplomatic', 'war'],
    }
    text_lower = text.lower()
    all_text = text_lower + ' ' + ' '.join(keywords).lower()
    for topic, keywords_list in topic_keywords.items():
        if any(kw in all_text for kw in keywords_list):
            topics.add(topic)
    return list(topics) if topics else ['General']

def is_valid_author_name(name, outlet_domain):
    """
    Validate author-like text and filter out placeholders / org names / timestamps.
    """
    if not name or len(name) < 3:
        return False
    name_lower = name.lower().strip()
    date_time_patterns = [
        r'\d{1,2}:\d{2}', r'\d{4}', r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)',
        r'(mins?|hours?|days?|ago)', r'\d{1,2}[/-]\d{1,2}', r'(monday|tuesday|wednesday|thursday|friday|saturday|sunday)',
        r'(am|pm)\b', r'updated', r'published', r'posted',
    ]
    if any(re.search(pattern, name_lower) for pattern in date_time_patterns):
        return False
    domain_parts = outlet_domain.replace('.com', '').replace('.in', '').replace('.', ' ').split()
    org_identifiers = [
        'news', 'times', 'post', 'daily', 'weekly', 'press', 'media', 'network',
        'broadcasting', 'corporation', 'bureau', 'desk', 'team', 'staff',
        'editorial', 'office', 'group', 'agency', 'service', 'channel',
        'tv', 'radio', 'online', 'digital', 'correspondent', 'reporter'
    ]
    for part in domain_parts:
        if len(part) > 2 and part in name_lower:
            if len(name_lower.split()) <= 2:
                return False
    for identifier in org_identifiers:
        if identifier in name_lower and len(name_lower.split()) <= 2:
            return False
    generic_patterns = [
        r'^(by|author|written by|posted by):?\s*$', r'^(the\s+)?[a-z]+\s+(bureau|desk|team|staff)$',
        r'^\w+\s+(news|times|post)$', r'^[A-Z]{2,6}(\s+[A-Z]{2,6})*$', r'^(photo|image|video|graphic|illustration)',
        r'(twitter|facebook|instagram|social media)', r'contributed', r'^web\s+(desk|team)', r'^input',
    ]
    for pattern in generic_patterns:
        if re.search(pattern, name_lower):
            return False
    words = name.split()
    if len(words) > 6:
        return False
    if len(words) == 1 and name.isupper() and len(name) <= 5:
        return False
    if not re.search(r'[a-zA-Z]{2,}', name):
        return False
    if len(words) >= 2:
        properly_capitalized = sum(1 for w in words if w and w[0].isupper()) >= len(words) * 0.6
        if properly_capitalized:
            return True
    if len(words) == 1 and 3 <= len(name) <= 15 and name[0].isupper():
        return True
    if ',' in name and len(words) >= 2:
        return True
    if 2 <= len(words) <= 4 and all(3 <= len(w) <= 20 for w in words):
        return True
    return False

def scrape_article(article_url, outlet_name, outlet_domain):
    """
    Enhanced article scraper with intelligent author filtering.
    Writes results to <sanitized_domain>_data.csv and updates domain_data.
    """
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    max_retries = 2
    response = None
    for attempt in range(max_retries):
        try:
            response = http_get(article_url, headers=headers, timeout=10, allow_redirects=True)
            if response and response.status_code == 200:
                break
            elif response and response.status_code == 403:
                headers["User-Agent"] = random.choice(USER_AGENTS)
                time.sleep(0.5)
        except Exception as e:
            logger.debug(f"[scrape_article] request attempt failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(0.5)
    if not response or response.status_code != 200:
        return
    soup = BeautifulSoup(response.text, "html.parser")
    # HEADLINE
    headline = None
    try:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                txt = script.string or script.get_text() or ""
                txt = txt.strip()
                if not txt:
                    continue
                # primary attempt
                try:
                    data = json.loads(txt)
                except Exception:
                    # fallback: try to extract the first JSON object from the script text
                    m = re.search(r'(\{.*\})', txt, flags=re.S)
                    data = json.loads(m.group(1)) if m else {}
                if isinstance(data, list):
                    data = data[0] if data else {}
                if isinstance(data, dict):
                    headline = data.get("headline") or data.get("name")
                    if headline and len(headline.split()) > 3:
                        break
            except Exception as e:
                logger.debug(f"[scrape_article] ld+json parse skipped: {e}")
                continue
    except Exception as e:
        logger.debug(f"[scrape_article] ld+json outer error: {e}")
        pass
    if not headline:
        for prop in ["og:title", "twitter:title"]:
            meta = (soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop}))
            if meta and meta.get("content"):
                headline = meta["content"].strip()
                if len(headline.split()) > 3:
                    break
    if not headline:
        article_tag = soup.find("article")
        if article_tag:
            h1 = article_tag.find("h1")
            if h1 and len(h1.get_text(strip=True).split()) > 3:
                headline = h1.get_text(strip=True)
    if not headline:
        headline_selectors = [
            {"name": "h1", "class_": re.compile(r"(headline|title|head|article[-_]title)", re.I)},
            {"name": "h1", "attrs": {"itemprop": "headline"}},
            {"name": "h2", "class_": re.compile(r"(headline|article[-_]title)", re.I)},
        ]
        for selector in headline_selectors:
            tag = soup.find(**selector)
            if tag:
                text = tag.get_text(strip=True)
                if len(text.split()) > 3:
                    headline = text
                    break
    if not headline:
        h1 = soup.find("h1")
        if h1:
            text = h1.get_text(strip=True)
            if len(text.split()) > 3 and len(text) < 200:
                headline = text
    if not headline:
        if soup.title and soup.title.string:
            title_text = soup.title.string.strip()
            for sep in [" | ", " - ", " – ", " :: "]:
                if sep in title_text:
                    parts = title_text.split(sep)
                    headline = max(parts, key=len).strip()
                    break
            if not headline:
                headline = title_text
    if not headline or len(headline.split()) < 3:
        return
    headline = re.sub(r'\s+', ' ', headline).strip()[:300]
    # AUTHOR EXTRACTION (multiple strategies)
    authors_found = set()
    try:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                if isinstance(data, list):
                    data = data[0] if data else {}
                if isinstance(data, dict):
                    author_data = data.get("author", [])
                    if isinstance(author_data, dict):
                        author_data = [author_data]
                    elif not isinstance(author_data, list):
                        author_data = []
                    for author in author_data:
                        if isinstance(author, dict) and "name" in author:
                            name = author["name"].strip()
                            if 2 <= len(name.split()) <= 6:
                                authors_found.add(name)
            except:
                continue
    except:
        pass
    for tag in soup.find_all("a", rel="author"):
        name = tag.get_text(strip=True)
        if 2 <= len(name.split()) <= 6:
            authors_found.add(name)
    for tag in soup.find_all(attrs={"itemprop": "author"}):
        name_tag = tag.find(attrs={"itemprop": "name"})
        if name_tag:
            name = name_tag.get_text(strip=True)
        else:
            name = tag.get_text(strip=True)
        if 2 <= len(name.split()) <= 6:
            authors_found.add(name)
    meta_author = soup.find("meta", {"name": "author"}) or soup.find("meta", {"property": "author"})
    if meta_author and meta_author.get("content"):
        name = meta_author["content"].strip()
        if 2 <= len(name.split()) <= 6:
            authors_found.add(name)
    author_class_patterns = [
        re.compile(r"(author|byline|writer|contributor|person[-_]name|posted[-_]by)", re.I)
    ]
    for pattern in author_class_patterns:
        for tag in soup.find_all(["a", "span", "div", "p"], class_=pattern):
            name = tag.get_text(strip=True)
            name = re.sub(r'^(by|written by|posted by|author|reporter):?\s*', '', name, flags=re.I)
            if 2 <= len(name.split()) <= 6:
                authors_found.add(name)
    for tag in soup.find_all("a", href=True):
        href = tag["href"].lower()
        if any(pattern in href for pattern in ["/author", "/writer", "/journalist", "/profile", "/by/"]):
            name = tag.get_text(strip=True)
            if 2 <= len(name.split()) <= 6:
                authors_found.add(name)
    for tag in soup.find_all(attrs={"data-author": True}):
        name = tag.get("data-author").strip()
        if 2 <= len(name.split()) <= 6:
            authors_found.add(name)
    for class_name in ["author-name", "byline-name", "contributor-name", "writer-name"]:
        for tag in soup.find_all(class_=class_name):
            name = tag.get_text(strip=True)
            if 2 <= len(name.split()) <= 6:
                authors_found.add(name)
    article_tag = soup.find("article")
    if article_tag:
        for tag in article_tag.find_all(["span", "div", "p"], class_=re.compile(r"author", re.I)):
            name = tag.get_text(strip=True)
            name = re.sub(r'^(by|author):?\s*', '', name, flags=re.I)
            if 2 <= len(name.split()) <= 6:
                authors_found.add(name)
    # Filter valid authors
    valid_authors = set()
    for author in authors_found:
        cleaned = author.strip()
        cleaned = re.sub(r'^(by|author|written by|posted by):?\s*', '', cleaned, flags=re.I)
        cleaned = cleaned.strip()
        if is_valid_author_name(cleaned, outlet_domain):
            valid_authors.add(cleaned)
    if not valid_authors:
        return
    keywords = extract_keywords_nlp(headline)
    topics = extract_topics(headline, keywords)
    csv_filename = f"{sanitize_filename(outlet_domain)}_data.csv"
    data_store = domain_data[outlet_domain]
    with data_store['lock']:
        for author in valid_authors:
            pair = (author.lower(), headline.lower())
            if pair not in data_store['seen_pairs']:
                data_store['seen_pairs'].add(pair)
                data_store['count'] += 1
                try:
                    file_exists = os.path.isfile(csv_filename)
                    with open(csv_filename, "a", newline="", encoding="utf-8") as csvfile:
                        writer = csv.writer(csvfile)
                        if not file_exists:
                            writer.writerow(["Author", "Headline", "Keywords", "Topics", "URL", "Outlet", "Timestamp"])
                        writer.writerow([
                            author,
                            headline,
                            ", ".join(keywords),
                            ", ".join(topics),
                            article_url,
                            outlet_name,
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        ])
                    logger.info(f"✓ [{outlet_domain}] {author} | {headline[:40]}...")
                except Exception as e:
                    logger.error(f"CSV write error: {e}")

# Create a shared session with retries to improve throughput and reliability
SESSION = requests.Session()
_retry = Retry(total=3, backoff_factor=0.3, status_forcelist=(429, 500, 502, 503, 504))
_adapter = HTTPAdapter(max_retries=_retry)
SESSION.mount("http://", _adapter)
SESSION.mount("https://", _adapter)

def http_get(url, headers=None, timeout=10, allow_redirects=True):
    """Thread-safe helper using SESSION with retries. Returns Response or None."""
    try:
        hdrs = headers or {"User-Agent": random.choice(USER_AGENTS)}
        resp = SESSION.get(url, headers=hdrs, timeout=timeout, allow_redirects=allow_redirects)
        return resp
    except Exception as e:
        logger.debug(f"[http_get] {url} failed: {e}")
        return None

def crawl_site(home_url, outlet_name="Unknown", max_articles=100, max_threads=12, max_depth=4):
    """
    Optimized crawler that uses scrape_article(...) and updates domain_data.
    """
    visited_urls = set()
    failed_urls = set()
    to_visit = [(home_url, 0)]
    base_domain = extract_domain(home_url)
    outlet_domain = base_domain
    lock = threading.Lock()
    article_indicators = [
        r'/article/', r'/story/', r'/news/',
        r'/\d{4}/\d{2}/\d{2}/', r'/\d{4}/\d{2}/',
        r'-\d{6,}\.html', r'/post/', r'/blog/',
        r'/\d{8}/', r'/read/', r'/detail/', r'/p/',
        r'/opinion/', r'/analysis/', r'/feature/',
    ]
    avoid_patterns = [
        r'\.(jpg|jpeg|png|gif|pdf|zip|mp4|mp3|avi|mov)$',
        r'/(tag|category|archive|login|register|subscribe|about|contact|privacy|terms)/',
        r'/page/\d+', r'#', r'\?share=', r'/feed/', r'/rss',
        r'/wp-admin/', r'/wp-content/', r'/search',
    ]
    def is_likely_article(url):
        url_lower = url.lower()
        if any(re.search(pattern, url_lower) for pattern in avoid_patterns):
            return False
        score = sum(1 for pattern in article_indicators if re.search(pattern, url_lower))
        path_depth = len(urlparse(url).path.strip('/').split('/'))
        if path_depth >= 3:
            score += 1
        return score > 0
    def clean_url(url):
        parsed = urlparse(url)
        if parsed.query:
            params = parse_qs(parsed.query)
            cleaned_params = {k: v for k, v in params.items() 
                              if k not in ['utm_source', 'utm_medium', 'utm_campaign', 'fbclid', 'gclid', 'ref', 'share']}
            query = urlencode(cleaned_params, doseq=True) if cleaned_params else ''
        else:
            query = ''
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}" + (f"?{query}" if query else "")
    def worker(url, depth):
        if depth > max_depth:
            return
        with lock:
            if domain_data[outlet_domain]['count'] >= max_articles:
                return
        time.sleep(random.uniform(0.2, 0.6))
        with lock:
            if url in visited_urls or url in failed_urls:
                return
            visited_urls.add(url)
        try:
            response = http_get(
                url,
                headers={"User-Agent": random.choice(USER_AGENTS)},
                timeout=10,
                allow_redirects=True
            )
            if not response or response.status_code != 200:
                with lock:
                    failed_urls.add(url)
                return
            content_type = response.headers.get('Content-Type', '').lower()
            if 'text/html' not in content_type:
                return
        except Exception as e:
            with lock:
                failed_urls.add(url)
            logger.debug(f"[crawl_site] fetch failed {url}: {e}")
            return
        soup = BeautifulSoup(response.text, "html.parser")
        found_links = []
        for a_tag in soup.find_all("a", href=True):
            try:
                href = a_tag["href"]
                full_url = urljoin(url, href)
                full_url = clean_url(full_url)
                parsed = urlparse(full_url)
                if parsed.netloc != base_domain:
                    continue
                if parsed.scheme not in ['http', 'https']:
                    continue
                with lock:
                    if full_url in visited_urls or full_url in failed_urls:
                        continue
                if is_likely_article(full_url):
                    found_links.insert(0, (full_url, depth + 1, 2))
                else:
                    found_links.append((full_url, depth + 1, 1))
            except:
                continue
        with lock:
            found_links.sort(key=lambda x: x[2], reverse=True)
            to_visit.extend([(u, d) for u, d, _ in found_links[:25]])
        if is_likely_article(url) or depth == 0:
            scrape_article(url, outlet_name, outlet_domain)
    logger.info(f"🚀 Starting FAST crawler for {outlet_name}")
    start_time = time.time()
    while to_visit and domain_data[outlet_domain]['count'] < max_articles:
        with lock:
            batch = to_visit[:max_threads * 3]
            to_visit = to_visit[max_threads * 3:]
        if not batch:
            break
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=max_threads) as executor:
            futures = [executor.submit(worker, url, depth) for url, depth in batch]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    pass
    elapsed = time.time() - start_time
    count = domain_data[outlet_domain]['count']
    logger.info(f"✅ Done! {count} articles in {elapsed:.1f}s → {sanitize_filename(outlet_domain)}_data.csv")

def csv_to_journalist_json(csv_path, output_json="journalist_data.json", top_n=10):
    # Step 1: Load the CSV
    df = pd.read_csv(csv_path)
    
    # Normalize column names
    df.columns = [c.strip().lower() for c in df.columns]
    
    # Validate presence of expected columns
    required_cols = ["author", "keywords", "topics"]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Missing column '{col}' in CSV")
    
    # Step 2: Group by Author
    journalist_data = {}
    grouped = df.groupby("author", dropna=True)
    
    for author, group in grouped:
        # Skip missing or anonymous authors
        if not isinstance(author, str) or not author.strip():
            continue
        
        # Article count
        article_count = len(group)
        
        # Collect all keywords and topics
        all_keywords = []
        all_topics = []
        
        for _, row in group.iterrows():
            if isinstance(row["keywords"], str):
                kws = [k.strip() for k in row["keywords"].split(",") if k.strip()]
                all_keywords.extend(kws)
            if isinstance(row["topics"], str):
                tps = [t.strip() for t in row["topics"].split(",") if t.strip()]
                all_topics.extend(tps)
        
        # Frequency counts
        keyword_counts = dict(Counter(all_keywords))
        topic_counts = dict(Counter(all_topics))
        
        journalist_data[author] = {
            "article_count": article_count,
            "keywords": keyword_counts,
            "topics": topic_counts
        }
    
    # Step 3: Identify Top Contributors
    top_contributors = sorted(
        [{"name": name, "article_count": data["article_count"]}
         for name, data in journalist_data.items()],
        key=lambda x: x["article_count"],
        reverse=True
    )[:top_n]
    
    # Step 4: Final JSON structure
    output = {
        "journalists": journalist_data,
        "top_contributors": top_contributors
    }
    
    # Step 5: Write to JSON file
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=4)
    
    print(f"✅ JSON file saved as: {output_json}")
    print(f"📈 Top {top_n} contributors:")
    for t in top_contributors:
        print(f"  {t['name']}: {t['article_count']} articles")
    return output, output_json

def build_bipartite_graph(journalist_data):
    B = nx.Graph()
    
    # Add nodes
    for journalist, info in journalist_data.items():
        B.add_node(journalist, bipartite=0)
        for topic in info["topics"]:
            B.add_node(topic, bipartite=1)
            B.add_edge(journalist, topic, weight=info["topics"][topic])
    
    # Draw the graph
    plt.figure(figsize=(12, 8))
    pos = nx.spring_layout(B, k=0.5)
    nx.draw(
        B, pos, with_labels=True, node_size=1500, node_color=['skyblue' if B.nodes[n]['bipartite']==0 else 'lightgreen' for n in B.nodes()],
        edge_color='gray', font_size=5
    )
    
    # Save to base64 to embed in HTML
    img = io.BytesIO()
    plt.savefig(img, format='png', bbox_inches='tight')
    plt.close()
    img.seek(0)
    graph_url = base64.b64encode(img.getvalue()).decode()
    return graph_url