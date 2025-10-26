from flask import Flask, render_template_string, request, render_template, send_file
import threading, os
from ddgs import DDGS
import logging
from dotenv import load_dotenv
from googleapiclient.discovery import build

# small local imports used by routes
import requests
from bs4 import BeautifulSoup

# Import core utilities
from app.core import (
    csv_to_journalist_json,
    build_bipartite_graph,
    extract_domain,
    sanitize_filename,
    crawl_site,
    domain_data,
)

# Load .env
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
SEARCH_ENGINE_ID = os.getenv("SEARCH_ENGINE_ID")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route('/')
def home():
    """Render the home page.

    Returns:
        A rendered template for the index/home page.
    """
    return render_template("index.html")


@app.route("/journalists", methods=["GET", "POST"])
def journalists():
    """Handle the combined search and dashboard route.

    This endpoint accepts either:
      - query: a media outlet name (will try to detect the official site using
        Google Custom Search and DuckDuckGo), or
      - url: the outlet's homepage (will be used directly).

    Behavior:
      - If neither query nor url is provided, returns a short help message.
      - Detects the outlet homepage when a query is provided.
      - Starts a background crawl (crawl_site) for the detected URL.
      - If a CSV for the outlet already exists, converts it to JSON and renders
        the journalists dashboard. Otherwise returns a "scraping started" page.

    Returns:
        Rendered template 'journalists.html' with context describing the outlet,
        CSV location, and (optionally) parsed journalist data and graph.
    """
    logger.info(f"[ROUTE] /journalists called with method={request.method}")

    # use request.values so we accept both GET params and POST form data
    query = request.values.get("query")
    url = request.values.get("url")
    title_from_search = request.values.get("title", "Unknown")

    if not query and not url:
        return "Provide ?query=Outlet Name or ?url=Outlet Homepage"

    result_url = None
    page_title = title_from_search

    # If query provided, try to detect official site
    if query:
        logger.info(f"[SEARCH] Query: {query}")
        # Google Custom Search
        try:
            if GOOGLE_API_KEY and SEARCH_ENGINE_ID:
                svc = build("customsearch", "v1", developerKey=GOOGLE_API_KEY)
                resp = svc.cse().list(q=query, cx=SEARCH_ENGINE_ID, num=5).execute()
                items = resp.get("items", []) or []
                if not items:
                    logger.warning(f"[SEARCH] No results returned from Google for query: {query}")
                for r in items:
                    u = r.get("link", "")
                    title = r.get("title", "")
                    domain = extract_domain(u)
                    if query.lower() in domain or query.lower() in title.lower():
                        result_url = u
                        page_title = title or page_title
                        logger.info(f"[SEARCH] Google matched: {u}")
                        break
        except Exception as e:
            logger.error(f"[SEARCH] Google Custom Search failed: {e}")

        # DuckDuckGo fallback
        if not result_url:
            try:
                with DDGS() as ddgs:
                    _ddg_results = list(ddgs.text(query, max_results=10))
                    ddg_results = list(filter(lambda i: "wiki" not in i["href"], _ddg_results))
                    for item in ddg_results:
                        if "href" not in item or "title" not in item:
                            continue
                        u = item["href"]
                        title = item["title"]
                        domain = extract_domain(u)
                        if query.lower() in domain or query.lower() in title.lower():
                            result_url = u
                            page_title = title or page_title
                            logger.info(f"[SEARCH] DuckDuckGo matched: {u}")
                            break
            except Exception as e:
                logger.debug(f"[SEARCH] DuckDuckGo failed: {e}")

        if not result_url:
            return f"No results found for '{query}'."

        url = result_url
    else:
        # url provided directly: try to fetch page title
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            page_title = soup.title.string if soup.title else page_title
        except Exception as e:
            return f"Error fetching page: {e}"

    outlet_name = page_title.split(" - ")[0].strip() if " - " in page_title else extract_domain(url)
    domain = extract_domain(url)
    csv_file = f"{sanitize_filename(domain)}_data.csv"

    # Start background crawl
    thread = threading.Thread(
        target=crawl_site,
        args=(url, outlet_name),
        kwargs={"max_articles": 100, "max_threads": 12, "max_depth": 4},
        daemon=True,
    )
    thread.start()
    logger.info(f"[CRAWL] Started crawl for {outlet_name} -> {url}")

    # Prepare context for template
    context = {
        "outlet_name": outlet_name,
        "source_url": url,
        "csv_file": csv_file,
        "journalists_dict": None,
        "top_contributors": None,
        "graph_img": None,
    }

    # If CSV exists attempt to parse and show dashboard
    if os.path.isfile(csv_file):
        try:
            output, json_path = csv_to_journalist_json(csv_file)
            context["journalists_dict"] = output.get("journalists", {})
            context["top_contributors"] = output.get("top_contributors", [])
            context["graph_img"] = build_bipartite_graph(context["journalists_dict"]) if context["journalists_dict"] else None
        except Exception as e:
            logger.debug(f"[DASHBOARD] CSV present but parsing failed: {e}")
            # keep context as scraping-in-progress

    return render_template("journalists.html", **context)

@app.route("/download_csv/<path:filename>")
def download_csv(filename):
    csv_path = f"/data/data/com.termux/files/home/newstrace/{filename}"
    return send_file(csv_path, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
    

    
