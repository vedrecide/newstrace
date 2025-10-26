# NewsTrace

NewsTrace is a lightweight media-intelligence & journalist-profiling prototype that:
- Detects an outlet homepage from a name (Google CSE + DuckDuckGo fallback)
- Crawls the site to extract author/headline pairs and saves them as CSV
- Builds a simple journalist dashboard and bipartite graph

### 🛠 Key files
- [`app/__main__.py`](app/__main__.py) — Flask app and UI routes
- [`app.core.crawl_site`](app/core.py) — crawler that discovers article URLs
- [`app.core.scrape_article`](app/core.py) — article scraper that writes CSV
- [`app.core.csv_to_journalist_json`](app/core.py) — CSV → dashboard JSON
- [`app.core.build_bipartite_graph`](app/core.py) — graph image builder
- [`app/templates/index.html`](app/templates/index.html) and [`app/templates/journalists.html`](app/templates/journalists.html)

### Quick start (local)
1. Clone repo and enter project root
2. Create virtualenv and activate
   - python3 -m venv .venv
   - source .venv/bin/activate  # mac/linux
3. Install deps
   - pip install -r requirements.txt
4. (Optional) If you want better keyword extraction, install spaCy model:
   - python -m spacy download en_core_web_sm
5. Provide API keys
   - Add Google Custom Search keys to `.env` (project already reads .env)
     - GOOGLE_API_KEY and SEARCH_ENGINE_ID (see [.env](.env))
6. Run the app
   - python -m app
7. Open http://127.0.0.1:5000 and enter an outlet name on the homepage

### Usage notes
- The crawler writes per-outlet CSVs named <sanitized-domain>_data.csv in the project root.
- Refresh the /journalists page after a minute to see parsed results (scraping runs in background).
- See [`app/__main__.py`](app/__main__.py) to tune crawl params (max_articles, max_threads, max_depth).

### Files & deps
- Requirements: [requirements.txt](requirements.txt)
- Optional: spaCy improves NLP but fallback heuristics are included.

### Quick features
- 🚀 Fast multi-threaded crawl via [`app.core.crawl_site`](app/core.py)
- 🧾 CSV export per outlet (easy to process)
- 📊 Simple dashboard + graph via [`app.core.build_bipartite_graph`](app/core.py)

### License & Ethics
- Only scrapes publicly available pages. Avoid scraping behind paywalls or private pages.