# ==============================================================================
# Purpose: Scrape and extract abortion news data from MNSBC (2020-2024)
# Main Author: Erika Salvador
# Contributors: Maigan Lafontant, Emilie Ward
# Date modified: 2025-04-20
# ==============================================================================

# ==============================================================================
# ABOUT
# ==============================================================================

# ==============================================================================
# ABOUT
# ==============================================================================

# This script collects abortion-related articles from NBC News, specifically from
# its Politics section. It performs the following:

#   - Routes all web requests through the Tor network, which anonymizes your 
#     internet traffic by bouncing it through multiple volunteer-run servers around 
#     the world. This helps prevent websites from tracking your IP address or location.
#   - Connects via a SOCKS5 proxy, which acts as a secure tunnel for sending 
#     requests through Tor.
#   - Loads the NBC News abortion politics page, scrolls through additional results,
#     and extracts article links dynamically using XPath.
#   - Visits each article page to extract the full text, any images, and all internal 
#     hyperlinks.
#   - Analyzes the article content by counting how often each word appears.
#   - Saves the results in a clean, structured JSON file.

# Why this is written in Python (not R):

# While this course primarily uses RStudio, we found that scraping websites—
# especially those with dynamic JavaScript content and strict robot protections—
# was significantly slower and less flexible in R. After exploring multiple R-based 
# solutions, we shifted to Python, which offered better tooling and much faster 
# performance for this kind of task.

# All of the data we collected is publicly available and NBC News does not prohibit 
# automated access to this content (robots are allowed). So while the scraping itself 
# is entirely legal, R’s available tools simply weren’t fast or flexible enough to 
# handle the scale we needed.

# Python supports asynchronous programming, which allowed us to download many articles 
# at once without waiting for each to finish sequentially.
# Furthermore, essential libraries like `curl_cffi` (for stealthy web requests), 
# `selenium_driverless` (for controlling a browser without launching a window), and 
# `stem` (for working with the Tor network) are only available in Python.

# ==============================================================================
# Install Libraries
# ==============================================================================

# ==============================================================================
# IMPORTS
# ------------------------------------------------------------------------------
# Organized by category: standard library, third-party libraries, and scraping tools
# ==============================================================================

# --- Standard Library ---
import asyncio
import json
import logging
import re
import time
from collections import Counter
from functools import wraps

# --- Natural Language Processing ---
import nltk
from nltk.tokenize import word_tokenize

# --- HTML Parsing ---
import lxml.html

# --- Tor Controller (Stem) ---
from stem import Signal
from stem.control import Controller

# --- Web Scraping + Async Rendering ---
from requests_html import AsyncHTMLSession  # Optional: useful for JS-rendered content

# --- Selenium Driverless + Automation ---
from selenium_driverless import webdriver
from selenium_driverless.types.by import By
from selenium_driverless.scripts.network_interceptor import (
    InterceptedRequest,
    NetworkInterceptor,
    Request,
    RequestPattern,
    RequestStages
)
from selenium.webdriver.remote.webelement import WebElement

# --- WebSocket Communication Exceptions ---
from cdp_socket.exceptions import CDPError

# ==============================================================================
# CONFIGURATION
# ------------------------------------------------------------------------------
# These are the core values used across the script.
# HOST is the localhost IP used by the Tor proxy.
# TOR_PORT is the port used by SOCKS5 proxy for routing traffic anonymously.
# CONTROL_PORT is the port that lets us control Tor (e.g., request a new identity).
# URL is the starting point for scraping NBC News abortion politics section.
# RED/RESET is used to color error logs red in the terminal.
# POST_URL_CONTENT_MATCH is used to filter intercepted requests for valid articles.
# ==============================================================================
HOST = "127.0.0.1"
TOR_PORT = 9001
CONTROL_PORT = 8999

URL = "https://www.nbcnews.com/politics/abortion-news"
POST_URL_CONTENT_MATCH = "www.nbcnews.com/politics/"

RED = "\033[91m"
RESET = "\033[0m"

# ==============================================================================
# LOGGING SETUP
# ------------------------------------------------------------------------------
# Logging is color-coded: errors show in red to help debugging.
# This sets up a global logger with stream output to terminal.
# ==============================================================================

class ColoredFormatter(logging.Formatter):
    def format(self, record):
        if record.levelno == logging.ERROR:
            record.msg = f"{RED}{record.msg}{RESET}"
        return super().format(record)

logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(ColoredFormatter('%(levelname)s - %(name)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# ==============================================================================
# DATA SHARING & REQUEST INTERCEPTION
# ------------------------------------------------------------------------------
# DataStore is used to share headers and cookies across tasks.
# InterceptRequest captures browser requests to replicate API headers.
# ==============================================================================

class DataStore:
    def __init__(self):
        self.data = {}
        self.lock = asyncio.Lock()
        self.queue = asyncio.Queue()

    async def update(self, new_data):
        async with self.lock:
            self.data.update(new_data)
            await self.queue.put(new_data)

    async def wait_for_add(self):
        return await self.queue.get()


class InterceptRequest:
    def __init__(self, store: DataStore):
        self.store = store

    async def __call__(self, data: InterceptedRequest):
        try:
            if POST_URL_CONTENT_MATCH in data.request.url:
                await self.store.update({
                    "url": data.request.url,
                    "params": data.request.params,
                    "headers": data.request.headers
                })
                logger.info(f"Intercepted: {data.request.url}")
            await data.continue_request(intercept_response=True)
        except Exception as e:
            logger.error(f"Intercept error: {e}")


# ==============================================================================
# HTML PARSER
# ------------------------------------------------------------------------------
# Extracts article URLs from the main MSNBC container by XPath.
# ==============================================================================

class HtmlDataExtractor:
    def __init__(self, html: str):
        self.tree = lxml.html.fromstring(html, parser=lxml.html.HTMLParser(recover=True))

    def extract_links(self):
        return [
            a.get("href")
            for a in self.tree.xpath('//div[contains(@class, "styles_itemsContainer")]'
                                     '//div[contains(@class, "wide-tease-item__wrapper ")]'
                                     '//div[contains(@class, "wide-tease-item__info-wrapper")]/a')
            if a.get("href")
        ]


# ==============================================================================
# ARTICLE SCRAPER
# ------------------------------------------------------------------------------
# Uses AsyncHTMLSession to visit each article using a Tor-routed proxy session.
# ==============================================================================

class NBCNewsScraper:
    def __init__(self, proxy_port=TOR_PORT):
        self.proxy_port = proxy_port
        self.session = AsyncHTMLSession()

    async def fetch_url(self, url):
        proxies = {
            'http': f'socks5h://{HOST}:{self.proxy_port}',
            'https': f'socks5h://{HOST}:{self.proxy_port}'
        }
        response = await self.session.get(url, proxies=proxies)
        return response

    async def run(self, url_list):
        return await asyncio.gather(*[self.fetch_url(url) for url in url_list])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.session.close()
        
# ==============================================================================
# TASK MANAGEMENT & TEXT UTILITIES
# ------------------------------------------------------------------------------
# TaskQueue schedules scraping jobs.
# Text utilities clean and tokenize HTML content from articles.
# ==============================================================================

from nltk.tokenize import word_tokenize
from collections import Counter

class TaskQueue:
    def __init__(self):
        self.queue = asyncio.Queue()

    async def put(self, task):
        await self.queue.put(task)

    async def get(self):
        return await self.queue.get()


async def create_word_count_dict(text):
    return dict(Counter([
        word.lower() for word in word_tokenize(text) if word.isalpha()
    ]))


async def extract_article_data(script_json: dict) -> dict:
    content = script_json["props"]["initialState"]["article"]["content"][0]
    raw_text = ''.join(content["content"]["text"])
    content["word_count"] = await create_word_count_dict(raw_text)
    return content

# ==============================================================================
# SCRAPER ROUTINES
# ------------------------------------------------------------------------------
# Loads article links from scrolling container and fetches HTML for each.
# ==============================================================================

async def gather_article_data(cookies, queue: TaskQueue, store: DataStore):
    results = []
    shared_data = await store.wait_for_add()

    async with NBCNewsScraper() as scraper:
        while True:
            task = await queue.get()
            if task == "exit":
                break

            responses = await scraper.run(task)
            for response in responses:
                try:
                    html = response.html.html
                    script = lxml.html.fromstring(html).xpath('//script[@id="__NEXT_DATA__"]/text()')[0]
                    structured = await extract_article_data(json.loads(script))
                    results.append(structured)
                except Exception as e:
                    logger.error(f"Failed to process article: {e}")

    with open("../data/nbc_news_data.json", "w") as f::
        json.dump(results, f, indent=2)

# ==============================================================================
# ARTICLE LINK SCROLLER
# ------------------------------------------------------------------------------
# Scrolls through MSNBC Politics page and queues article links.
# ==============================================================================
async def load_more_and_queue(driver, queue: TaskQueue):
    extractor = HtmlDataExtractor(await driver.page_source)
    seen_links = set()

    for _ in range(8):  # Scroll + click loop
        try:
            button = await driver.find_element(By.XPATH, "//button[contains(@class, 'animated-ghost-button')]")
            await driver.sleep(1)
            await button.click()
            new_links = extractor.extract_links()
            unique = list(set(new_links) - seen_links)
            await queue.put(unique)
            seen_links.update(new_links)
            logger.info(f"Queued {len(unique)} new links")
        except Exception as e:
            logger.error(f"Error loading more articles: {e}")

    await queue.put("exit")


# ==============================================================================
# MAIN ENTRYPOINT
# ------------------------------------------------------------------------------
# Launches headless browser, connects Tor proxy, starts scraping flow.
# ==============================================================================
async def main():
    store = DataStore()
    queue = TaskQueue()

    options = webdriver.ChromeOptions()
    options.headless = True  # Optional: change to False for debugging

    async with webdriver.Chrome(options=options) as driver:
        await driver.set_single_proxy(proxy=f"socks5://{HOST}:{TOR_PORT}")

        async with NetworkInterceptor(driver, on_request=InterceptRequest(store),
                                      patterns=[RequestPattern.AnyRequest], intercept_auth=True):
            try:
                await driver.get(URL, wait_load=True)
                cookies = await driver.get_cookies()
                await asyncio.gather(
                    load_more_and_queue(driver, queue),
                    gather_article_data(cookies, queue, store)
                )
                logger.info("Scraping complete. Data saved.")
            except Exception as e:
                logger.error(f"Main flow error: {e}")


# ==============================================================================
# EXECUTION WRAPPER
# ==============================================================================
if __name__ == "__main__":
    import time
    import nltk
    nltk.download("punkt")

    print("Starting MSNBC Scraper via Tor...")
    start = time.perf_counter()
    asyncio.run(main())
    end = time.perf_counter()
    print(f"Done in {end - start:.2f} seconds")
