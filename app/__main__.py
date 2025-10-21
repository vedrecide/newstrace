from flask import Flask, render_template_string, request
from ddgs import DDGS
import requests
import logging
from bs4 import BeautifulSoup
import time


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
    old_query = request.args.get("query")
    query = old_query + " official news channel"
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
    

    urls_list = [r['href'] for r in results]  # collect all URLs
    print(urls_list)
        

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

    
    # runs only if search is complete
    if len(results) == 5:
        pick_best_result(results)
       
    return render_template_string(html, query=query, results=results)


avoid_keywords = ["reddit", "youtube", "twitter", "facebook", "linkedin", "wikipedia"]

def pick_best_result(results):
    for r in results:
        title = r["title"].lower()
        href = r["href"].lower()

        # Skip known irrelevant sites
        if any(word in href for word in avoid_keywords):
            continue
        #calling scrape for first irrelevent result
        else:
            scrape(r["href"])
       
   
    


def scrape(url):

    headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}
    
    
    response = requests.get(url, headers=headers, timeout=10)


    print(url)
    html = response.text

    soup = BeautifulSoup(html, 'html.parser')

    print(soup)

    with open("soup.txt","w") as f:
        f.write(url + soup.prettify())


if __name__ == '__main__':
    app.run(debug=True)
    