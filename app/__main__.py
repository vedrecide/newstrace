from flask import Flask, render_template_string, request, render_template
from ddgs import DDGS
import requests, os
import logging
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs
from dotenv import load_dotenv
from googleapiclient.discovery import build
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import csv
from datetime import datetime
import re
from collections import Counter, defaultdict
import json

# NLP Libraries
try:
    import spacy
    from spacy.lang.en.stop_words import STOP_WORDS
    nlp = spacy.load("en_core_web_sm")
    NLP_AVAILABLE = True
except:
    NLP_AVAILABLE = False
    print("⚠️  spaCy not available. Install with: pip install spacy && python -m spacy download en_core_web_sm")
    print("⚠️  Keyword extraction will use fallback method")

# Load .env file
load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
SEARCH_ENGINE_ID = os.getenv("SEARCH_ENGINE_ID")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

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

@app.route('/')
def home():
    return render_template("index.html")

def extract_domain(url):
    try:
        domain = urlparse(url).netloc.lower()
        # Clean domain for filename
        domain = domain.replace('www.', '')
        return domain
    except:
        return "unknown"

def sanitize_filename(domain):
    """Convert domain to valid filename"""
    # Remove invalid characters
    sanitized = re.sub(r'[^\w\-.]', '_', domain)
    return sanitized

@app.route('/results')
def search_results():
    query = request.args.get('query')
    if not query:
        return "No search query provided!"

    print(f"[DEBUG] Search query received: {query}")
    results = []

    # Stage-1: Try Google API search
    print("[DEBUG] Trying Google search...")
    try:
        service = build("customsearch", "v1", developerKey=GOOGLE_API_KEY)
        response = service.cse().list(q=query, cx=SEARCH_ENGINE_ID, num=5).execute()
        items = response.get("items", [])

        print(f"[DEBUG] Google returned {len(items)} results.")

        for r in items:
            url = r.get("link", "")
            title = r.get("title", "")
            domain = extract_domain(url)

            if query.lower() in domain or query.lower() in title.lower():
                results.append({"href": url, "title": title})
                print(f"[DEBUG] Google MATCH Found: {url}")
                break
    except Exception as e:
        print(f"[ERROR] Google search failed: {e}")

    # Stage-2: DuckDuckGo Fallback
    if not results:
        print("[DEBUG] Google did not return a valid match. Trying DuckDuckGo...")
        try:
            with DDGS() as ddgs:
                ddg_results = list(ddgs.text(query, max_results=10))
                print(f"[DEBUG] DuckDuckGo returned {len(ddg_results)} results.")

                for item in ddg_results:
                    if "href" not in item or "title" not in item:
                        continue
                    url = item["href"]
                    title = item["title"]
                    domain = extract_domain(url)

                    if query.lower() in domain or query.lower() in title.lower():
                        results.append({"href": url, "title": title})
                        print(f"[DEBUG] DuckDuckGo MATCH Found: {url}")
                        break
        except Exception as e:
            print(f"[ERROR] DuckDuckGo search failed: {e}")

    if not results:
        return f"No results found for '{query}'. Please try again."

    result = results[0]
    print(f"[DEBUG] Final Result: {result['href']}")

    html = '''
        <h2>Match Found for {{ query }}</h2>
        <ol>
            <li>
                <a href="/scrape?url={{ result.href|urlencode }}&title={{ result.title|urlencode }}">
                    {{ result.title }}
                </a><br>
                <small>{{ result.href }}</small>
            </li>
        </ol>
        <a href="/">Back</a>
    '''

    return render_template_string(html, query=query, result=result)


def extract_keywords_nlp(text):
    """Extract keywords using spaCy NLP"""
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


def extract_keywords_fallback(text):
    """Fallback keyword extraction"""
    if not text:
        return []
    
    stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 
                  'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'been', 'be',
                  'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
                  'should', 'may', 'might', 'must', 'can', 'this', 'that', 'these', 'those'}
    
    words = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b|\b[a-z]{4,}\b', text)
    filtered_words = [w.title() for w in words if w.lower() not in stop_words and len(w) > 3]
    word_freq = Counter(filtered_words)
    return [word for word, _ in word_freq.most_common(10)]


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
    INTELLIGENT AUTHOR NAME VALIDATION
    Filters out fake/placeholder names
    """
    if not name or len(name) < 3:
        return False
    
    name_lower = name.lower().strip()
    
    # === RULE 1: Check for date/time patterns ===
    date_time_patterns = [
        r'\d{1,2}:\d{2}',  # Time: 06:11
        r'\d{4}',          # Year: 2025
        r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)',  # Month names
        r'(mins?|hours?|days?|ago)',  # Duration indicators
        r'\d{1,2}[/-]\d{1,2}',  # Date: 10/26 or 10-26
        r'(monday|tuesday|wednesday|thursday|friday|saturday|sunday)',  # Day names
        r'(am|pm)\b',  # AM/PM
        r'updated',
        r'published',
        r'posted',
    ]
    
    if any(re.search(pattern, name_lower) for pattern in date_time_patterns):
        return False
    
    # === RULE 2: Check for outlet/organization names ===
    # Extract keywords from domain
    domain_parts = outlet_domain.replace('.com', '').replace('.in', '').replace('.', ' ').split()
    
    # Common organization identifiers
    org_identifiers = [
        'news', 'times', 'post', 'daily', 'weekly', 'press', 'media', 'network',
        'broadcasting', 'corporation', 'bureau', 'desk', 'team', 'staff',
        'editorial', 'office', 'group', 'agency', 'service', 'channel',
        'tv', 'radio', 'online', 'digital', 'correspondent', 'reporter'
    ]
    
    # Check if name contains domain parts (like "BBC" in "BBC News")
    for part in domain_parts:
        if len(part) > 2 and part in name_lower:
            # Exception: if it's a full name WITH domain (e.g., "John Smith - BBC")
            if len(name_lower.split()) <= 2:
                return False
    
    # Check if name is just an organization
    for identifier in org_identifiers:
        if identifier in name_lower and len(name_lower.split()) <= 2:
            return False
    
    # === RULE 3: Check for generic patterns ===
    generic_patterns = [
        r'^(by|author|written by|posted by):?\s*$',
        r'^(the\s+)?[a-z]+\s+(bureau|desk|team|staff)$',
        r'^\w+\s+(news|times|post)$',  # "ABC News"
        r'^[A-Z]{2,6}(\s+[A-Z]{2,6})*$',  # All caps like "ABP NEWS"
        r'^(photo|image|video|graphic|illustration)',
        r'(twitter|facebook|instagram|social media)',
        r'contributed',
        r'^web\s+(desk|team)',
        r'^input',
    ]
    
    for pattern in generic_patterns:
        if re.search(pattern, name_lower):
            return False
    
    # === RULE 4: Validate structure (must look like a name) ===
    words = name.split()
    
    # Too many words (likely not a name)
    if len(words) > 6:
        return False
    
    # Single word that's all caps and short (likely abbreviation)
    if len(words) == 1 and name.isupper() and len(name) <= 5:
        return False
    
    # Must have at least some alphabetic characters
    if not re.search(r'[a-zA-Z]{2,}', name):
        return False
    
    # === RULE 5: Positive indicators (looks like a real name) ===
    # Has proper name structure (capitalized words)
    if len(words) >= 2:
        # Check if words are properly capitalized (Title Case)
        properly_capitalized = sum(1 for w in words if w[0].isupper()) >= len(words) * 0.6
        if properly_capitalized:
            return True
    
    # Single word but seems like a surname/byline
    if len(words) == 1 and 3 <= len(name) <= 15 and name[0].isupper():
        return True
    
    # Has comma (often in "Last, First" format)
    if ',' in name and len(words) >= 2:
        return True
    
    # Final check: if 2-4 words with reasonable length
    if 2 <= len(words) <= 4 and all(3 <= len(w) <= 20 for w in words):
        return True
    
    return False


def scrape_article(article_url, outlet_name, outlet_domain):
    """
    ENHANCED ARTICLE SCRAPER with intelligent author filtering
    """
    
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    
    # === MAKE REQUEST WITH RETRY ===
    max_retries = 2
    response = None
    
    for attempt in range(max_retries):
        try:
            response = requests.get(
                article_url, 
                headers=headers, 
                timeout=10,
                allow_redirects=True
            )
            if response.status_code == 200:
                break
            elif response.status_code == 403:
                headers["User-Agent"] = random.choice(USER_AGENTS)
                time.sleep(0.5)
        except:
            if attempt < max_retries - 1:
                time.sleep(0.5)
    
    if not response or response.status_code != 200:
        return
    
    soup = BeautifulSoup(response.text, "html.parser")
    
    # === HEADLINE EXTRACTION ===
    headline = None
    
    # JSON-LD
    try:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                if isinstance(data, list):
                    data = data[0] if data else {}
                if isinstance(data, dict):
                    headline = data.get("headline") or data.get("name")
                    if headline and len(headline.split()) > 3:
                        break
            except:
                continue
    except:
        pass
    
    # Meta tags
    if not headline:
        for prop in ["og:title", "twitter:title"]:
            meta = (soup.find("meta", property=prop) or 
                   soup.find("meta", attrs={"name": prop}))
            if meta and meta.get("content"):
                headline = meta["content"].strip()
                if len(headline.split()) > 3:
                    break
    
    # Article h1
    if not headline:
        article_tag = soup.find("article")
        if article_tag:
            h1 = article_tag.find("h1")
            if h1 and len(h1.get_text(strip=True).split()) > 3:
                headline = h1.get_text(strip=True)
    
    # Headline classes
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
    
    # First h1
    if not headline:
        h1 = soup.find("h1")
        if h1:
            text = h1.get_text(strip=True)
            if len(text.split()) > 3 and len(text) < 200:
                headline = text
    
    # Title fallback
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
    
    # === AUTHOR EXTRACTION ===
    authors_found = set()
    
    # JSON-LD
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
    
    # rel="author"
    for tag in soup.find_all("a", rel="author"):
        name = tag.get_text(strip=True)
        if 2 <= len(name.split()) <= 6:
            authors_found.add(name)
    
    # itemprop="author"
    for tag in soup.find_all(attrs={"itemprop": "author"}):
        name_tag = tag.find(attrs={"itemprop": "name"})
        if name_tag:
            name = name_tag.get_text(strip=True)
        else:
            name = tag.get_text(strip=True)
        if 2 <= len(name.split()) <= 6:
            authors_found.add(name)
    
    # Meta author
    meta_author = soup.find("meta", {"name": "author"}) or soup.find("meta", {"property": "author"})
    if meta_author and meta_author.get("content"):
        name = meta_author["content"].strip()
        if 2 <= len(name.split()) <= 6:
            authors_found.add(name)
    
    # Author classes
    author_class_patterns = [
        re.compile(r"(author|byline|writer|contributor|person[-_]name|posted[-_]by)", re.I)
    ]
    
    for pattern in author_class_patterns:
        for tag in soup.find_all(["a", "span", "div", "p"], class_=pattern):
            name = tag.get_text(strip=True)
            name = re.sub(r'^(by|written by|posted by|author|reporter):?\s*', '', name, flags=re.I)
            if 2 <= len(name.split()) <= 6:
                authors_found.add(name)
    
    # Author links
    for tag in soup.find_all("a", href=True):
        href = tag["href"].lower()
        if any(pattern in href for pattern in ["/author", "/writer", "/journalist", "/profile", "/by/"]):
            name = tag.get_text(strip=True)
            if 2 <= len(name.split()) <= 6:
                authors_found.add(name)
    
    # data-author
    for tag in soup.find_all(attrs={"data-author": True}):
        name = tag.get("data-author").strip()
        if 2 <= len(name.split()) <= 6:
            authors_found.add(name)
    
    # Common classes
    for class_name in ["author-name", "byline-name", "contributor-name", "writer-name"]:
        for tag in soup.find_all(class_=class_name):
            name = tag.get_text(strip=True)
            if 2 <= len(name.split()) <= 6:
                authors_found.add(name)
    
    # Article tag authors
    article_tag = soup.find("article")
    if article_tag:
        for tag in article_tag.find_all(["span", "div", "p"], class_=re.compile(r"author", re.I)):
            name = tag.get_text(strip=True)
            name = re.sub(r'^(by|author):?\s*', '', name, flags=re.I)
            if 2 <= len(name.split()) <= 6:
                authors_found.add(name)
    
    # === INTELLIGENT AUTHOR FILTERING ===
    valid_authors = set()
    for author in authors_found:
        # Clean
        cleaned = author.strip()
        cleaned = re.sub(r'^(by|author|written by|posted by):?\s*', '', cleaned, flags=re.I)
        cleaned = cleaned.strip()
        
        # Validate using intelligent filter
        if is_valid_author_name(cleaned, outlet_domain):
            valid_authors.add(cleaned)
    
    if not valid_authors:
        return
    
    # === EXTRACT KEYWORDS & TOPICS ===
    keywords = extract_keywords_nlp(headline)
    topics = extract_topics(headline, keywords)
    
    # === SAVE TO DOMAIN-SPECIFIC CSV ===
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


def crawl_site(home_url, outlet_name="Unknown", max_articles=100, max_threads=12, max_depth=4):
    """
    OPTIMIZED HIGH-SPEED CRAWLER
    """
    
    visited_urls = set()
    failed_urls = set()
    to_visit = [(home_url, 0)]
    base_domain = extract_domain(home_url)
    outlet_domain = base_domain
    lock = threading.Lock()
    
    # URL patterns
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
                            if k not in ['utm_source', 'utm_medium', 'utm_campaign', 
                                        'fbclid', 'gclid', 'ref', 'share']}
            from urllib.parse import urlencode
            query = urlencode(cleaned_params, doseq=True) if cleaned_params else ''
        else:
            query = ''
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}" + (f"?{query}" if query else "")
    
    def worker(url, depth):
        if depth > max_depth:
            return
        
        # Check article count
        with lock:
            if domain_data[outlet_domain]['count'] >= max_articles:
                return
        
        # Reduced delay for speed
        time.sleep(random.uniform(0.2, 0.6))
        
        with lock:
            if url in visited_urls or url in failed_urls:
                return
            visited_urls.add(url)
        
        try:
            response = requests.get(
                url,
                headers={"User-Agent": random.choice(USER_AGENTS)},
                timeout=10,
                allow_redirects=True
            )
            
            if response.status_code != 200:
                with lock:
                    failed_urls.add(url)
                return
            
            content_type = response.headers.get('Content-Type', '').lower()
            if 'text/html' not in content_type:
                return
            
        except:
            with lock:
                failed_urls.add(url)
            return
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Extract links
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
        
        # Scrape
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
        
        with ThreadPoolExecutor(max_workers=max_threads) as executor:
            futures = [executor.submit(worker, url, depth) for url, depth in batch]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    pass
    
    elapsed = time.time() - start_time
    count = domain_data[outlet_domain]['count']
    logger.info(f"✅ Done! {count} articles in {elapsed:.1f}s → {sanitize_filename(outlet_domain)}_data.csv")


@app.route('/scrape')
def scrape():
    url = request.args.get('url')
    title_from_search = request.args.get('title', 'Unknown page')

    if not url:
        return "No URL provided!"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except Exception as e:
        return f"Error fetching page: {e}"

    soup = BeautifulSoup(response.text, 'html.parser')
    title = soup.title.string if soup.title else title_from_search
    paragraphs = [p.get_text(strip=True) for p in soup.find_all('p')[:5]]

    html = '''
        <h2>Scraped Page</h2>
        <p><strong>Source:</strong> <a href="{{ url }}" target="_blank">{{ url }}</a></p>
        <p><strong>Title:</strong> {{ title }}</p>
        <h3>First few paragraphs:</h3>
        <ul>
            {% for p in paragraphs %}
                <li>{{ p }}</li>
            {% endfor %}
        </ul>
        <p><strong>Status:</strong> Scraping in progress... Check <code>{{ csv_file }}</code> for results.</p>
        <a href="javascript:history.back()">⬅ Back to results</a>
    '''

    outlet_name = title.split(' - ')[0].strip() if ' - ' in title else extract_domain(url)
    domain = extract_domain(url)
    csv_file = f"{sanitize_filename(domain)}_data.csv"
    
    # Start crawling
    thread = threading.Thread(
        target=crawl_site,
        args=(url, outlet_name),
        kwargs={'max_articles': 100, 'max_threads': 12, 'max_depth': 4}
    )
    thread.daemon = True
    thread.start()
    
    return render_template_string(html, url=url, title=title, paragraphs=paragraphs, csv_file=csv_file)


if __name__ == '__main__':
    app.run(debug=True, threaded=True)
