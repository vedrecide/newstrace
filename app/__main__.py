from flask import Flask, render_template_string, request, render_template
from ddgs import DDGS
import logging
from dotenv import load_dotenv
from googleapiclient.discovery import build

# Keep small local imports used by routes
import requests
from bs4 import BeautifulSoup

# Import core scraper/crawler utilities and variables
from app.core import (
    extract_domain,
    sanitize_filename,
    extract_keywords_nlp,
    extract_keywords_fallback,
    extract_topics,
    is_valid_author_name,
    scrape_article,
    crawl_site,
    domain_data,
    USER_AGENTS,
    NLP_AVAILABLE
)

import os
# import random
# import time
import threading

# Load .env file
load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
SEARCH_ENGINE_ID = os.getenv("SEARCH_ENGINE_ID")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
def home():
    return render_template("index.html")

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
    
    # Start crawling using imported crawl_site
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
