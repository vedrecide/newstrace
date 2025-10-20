from flask import Flask, render_template_string, request
from ddgs import DDGS
import requests
import logging
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.DEBUG) 
app = Flask(__name__)

@app.route('/')
def home():
    return '''
        <h2>Search</h2>
        <form action="/results" method="get">
            <input type="text" name="query" placeholder="Search something..." style="width:300px;">
            <button type="submit">Search</button>
        </form>
    '''

@app.route('/results')
def search_results():
    query = request.args.get('query')
    if not query:
        return "No search query provided!"

    try:
        with DDGS() as ddgs:
            original_results = list(ddgs.text(query, max_results=8))
            results = list(filter(lambda i: query in i["href"].split("/")[2], original_results))
    except Exception as e:
        return f"Error performing search: {e}"

    if not results:
        return f"No results found for '{query}'."

    html = '''
        <h2>For "{{ query }}", Do you mean?</h2>
        <ol>
            {% for r in results %}
                <li>
                    <a href="/scrape?url={{ r.href|urlencode }}&title={{ r.title|urlencode }}">
                        {{ r.title }}
                    </a><br>
                    <small>{{ r.href }}</small>
                </li>
            {% endfor %}
        </ol>
        <a href="/">Back</a>
    '''
    return render_template_string(html, query=query, results=results)

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
    return render_template_string(html, url=url, title=title, paragraphs=paragraphs)

if __name__ == '__main__':
    app.run(debug=True)