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
            results = list(ddgs.text(query, max_results=5))
            # app.logger.info(results)
    except Exception as e:
        return f"Error performing search: {e}"

    if not results:
        return f"No results found for '{query}'."

    html = '''
        <h2>Search results for "{{ query }}"</h2>
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


if __name__ == '__main__':
    app.run(debug=True)