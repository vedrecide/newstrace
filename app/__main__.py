from flask import Flask, render_template_string, request, render_template
from ddgs import DDGS
import requests, os
import logging
from bs4 import BeautifulSoup
from urllib.parse import urljoin,urlparse
from dotenv import load_dotenv
from googleapiclient.discovery import build

authors = []
url_list = []
authors_headlines = []
seen_pairs = set()  

# Load .env file
load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
SEARCH_ENGINE_ID = os.getenv("SEARCH_ENGINE_ID")


logging.basicConfig(level=logging.DEBUG) 
app = Flask(__name__)

@app.route('/')
def home():

    return render_template("index.html")

def extract_domain(url):
    try:
        return urlparse(url).netloc.lower()
    except:
        return ""

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
                break  # Take the first strong match
    except Exception as e:
        print(f"[ERROR] Google search failed: {e}")

    # Stage-2: DuckDuckGo Fallback if no match from Google
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

    # Only single best result
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

# @app.route('/scrape')
# def scrape():
#     url = request.args.get('url')
#     title_from_search = request.args.get('title', 'Unknown page')

#     if not url:
#         return "No URL provided!"

#     try:
#         response = requests.get(url, timeout=10)
#         response.raise_for_status()
#     except Exception as e:
#         return f"Error fetching page: {e}"

#     soup = BeautifulSoup(response.text, 'html.parser')
#     title = soup.title.string if soup.title else title_from_search
#     paragraphs = [p.get_text(strip=True) for p in soup.find_all('p')[:5]]

#     html = '''
#         <h2>Scraped Page</h2>
#         <p><strong>Source:</strong> <a href="{{ url }}" target="_blank">{{ url }}</a></p>
#         <p><strong>Title:</strong> {{ title }}</p>
#         <h3>First few paragraphs:</h3>
#         <ul>
#             {% for p in paragraphs %}
#                 <li>{{ p }}</li>
#             {% endfor %}
#         </ul>
#         <a href="javascript:history.back()">⬅ Back to results</a>
#     '''
#     return render_template_string(html, url=url, title=title, paragraphs=paragraphs)




def scrape_article(article_url):
    """
    Scrape a single article page:
    - Extract headline (h1/h2/h3 or og:title)
    - Extract authors in two ways:
        1. <a> tags with href containing '/authors'
        2. Common author classes or meta tags
    - Writes unique pairs to file
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    avoid_names = ["Published By:","Written By:", "Authors & Contributors", "The Hindu Bureau ", "HT","Aaj Tak"]

    try:
        resp = requests.get(article_url, headers=headers, timeout=10)
        if not resp.ok:
            print("Failed to fetch:", article_url)
            return

        soup = BeautifulSoup(resp.text, "html.parser")

        # Try to find a headline
        headline = None
        for tag_name in ["h1", "h2", "h3"]:
            tag = soup.find(tag_name)
            if tag and len(tag.get_text(strip=True).split()) > 2:
                headline = tag.get_text(strip=True)
                break

        if not headline:
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                headline = og_title["content"].strip()

        if not headline:
            print("No headline found:", article_url)
            return

        # --- Find authors ---
        authors = set()

        # Links with '/authors'
        for tag in soup.find_all("a", href=True):
            if "/authors" in tag["href"]:
                text = tag.get_text(strip=True)
                if 2 <= len(text.split()) <= 5: # likely a person name
                    text_clean = text.strip()
                    if any(name.lower() in text_clean.lower() for name in avoid_names):
                        continue  # skip


        # common author classes
        candidate_classes = ["author", "byline", "writer", "contributor", "person-name"]
        for tag in soup.find_all(["a", "span", "div"], class_=lambda c: c and any(x in c.lower() for x in candidate_classes)):
            text = tag.get_text(strip=True)
            if 2 <= len(text.split()) <= 5:  # likely a person name
                text_clean = text.strip()
                if any(name.lower() in text_clean.lower() for name in avoid_names):
                        continue  # skip

        # Meta author fallback
        meta_author = soup.find("meta", {"name": "author"})
        if meta_author and meta_author.get("content"):
            authors.add(meta_author["content"].strip())

        if not authors:
            print("No authors found:", article_url)
            return

        # Write unique pairs
        with open("authors_headlines.txt", "a", encoding="utf-8") as f:
            for author in authors:
                pair = (author, headline)
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    f.write(f"{author} || {headline}\n")
                    print("Saved:", author, "||", headline)

    except Exception as e:
        print("Error scraping article:", article_url, e)


def crawl_site(home_url, max_authors=30):
    """
    Crawl a news site starting from home_url.
    - Collect internal article links
    - Scrapes until max_authors unique author-headline pairs are reached
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    visited = set()
    to_visit = [home_url]
    base_domain = urlparse(home_url).netloc

    while to_visit and len(seen_pairs) < max_authors:
        current_url = to_visit.pop(0)
        if current_url in visited:
            continue
        visited.add(current_url)

        # Skip obvious non-article pages
        skip_keywords = ["about", "contact", "privacy", "terms", "advertise", "subscribe"]
        if any(kw in current_url.lower() for kw in skip_keywords):
            continue

        try:
            resp = requests.get(current_url, headers=headers, timeout=10)
            if not resp.ok:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")

            # Collect internal links
            for a in soup.find_all("a", href=True):
                full_url = urljoin(home_url, a["href"])
                parsed = urlparse(full_url)
                if parsed.netloc == base_domain and full_url not in visited and full_url.startswith(("http://", "https://")):
                    to_visit.append(full_url)

            # Scrape the current page
            scrape_article(current_url)

        except Exception as e:
            print("Error visiting page:", current_url, e)
            continue

    print(f"Finished. Collected {len(seen_pairs)} unique author-headline pairs.")
    visited.clear()
    seen_pairs.clear()
    to_visit.clear()



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
        <a href="javascript:history.back()">⬅ Back to results</a>
    '''

    crawl_site(url)
    return render_template_string(html, url=url, title=title, paragraphs=paragraphs)


if __name__ == '__main__':
    app.run(debug=True)
    
    