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
    build_bipartite_graph,
    csv_to_journalist_json,
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
        kwargs={'max_articles': 100, 'max_threads': 12, 'max_depth': 400}
    )
    thread.daemon = True
    thread.start()
    
    return render_template_string(html, url=url, title=title, paragraphs=paragraphs, csv_file=csv_file)

@app.route("/journalists")
def journalists():
    base_dir = os.getcwd()  # or dirname(__file__) for script location
    csv_path = os.path.join(base_dir, "timesofindia.indiatimes.com_data.csv")
    data, json_file = csv_to_journalist_json(csv_path)
    
    # Extract only the journalists dict
    journalists_dict = data['journalists']
    
    # Get top contributors
    top_contributors = sorted(journalists_dict.items(), key=lambda x: x[1]['article_count'], reverse=True)[:5]
    
    # Build graph
    graph_img = build_bipartite_graph(journalists_dict)
    
    # HTML
    html = "<h1>Journalists Overview</h1>"
    html += "<h2>Top Contributors</h2><ul>"
    for name, info in top_contributors:
        html += f"<li>{name}: {info['article_count']} articles</li>"
    html += "</ul>"
    
    html += "<h2>Journalists Table</h2><table border='1'><tr><th>Name</th><th>Articles</th><th>Top Topics</th><th>Top Keywords</th></tr>"
    for name, info in journalists_dict.items():  # <- FIXED HERE
        top_topics = ", ".join([f"{k}({v})" for k, v in sorted(info["topics"].items(), key=lambda x: -x[1])[:3]])
        top_keywords = ", ".join([f"{k}({v})" for k, v in sorted(info["keywords"].items(), key=lambda x: -x[1])[:5]])
        html += f"<tr><td>{name}</td><td>{info['article_count']}</td><td>{top_topics}</td><td>{top_keywords}</td></tr>"
    html += "</table>"
    
    html += "<h2>Journalist-Topic Graph</h2>"
    html += f"<img src='data:image/png;base64,{graph_img}'/>"
    
    return html


if __name__ == '__main__':
    app.run(debug=True)
    

    
