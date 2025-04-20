# ==============================================================================
# Purpose: Scrape and extract abortion news data from Fox News (2020-2024)
# Main Author: Erika Salvador
# Contributors: Maigan Lafontant, Emilie Ward
# Date modified: 2025-03-25
# ==============================================================================

# ==============================================================================
# ABOUT
# ==============================================================================

# This script collects news articles from the Fox News website, specifically those 
# related to abortion in the politics section. It does the following:

#   - Routes all web requests through the Tor network, which anonymizes your 
#     internet traffic by bouncing it through multiple volunteer-run servers around 
#     the world. This helps prevent websites from tracking your IP address or location.
#   - Connects via a SOCKS5 proxy, which acts as a secure tunnel for sending 
#     requests through Tor.
#   - Intercepts and uses Fox News’s internal API to collect article metadata 
#     (like titles and URLs).
#   - Visits each article page to extract the full text, any images, and all internal 
#     hyperlinks.
#   - Analyzes the content by counting how often each word appears.
#   - Saves the results in a clean, structured JSON file.

# Why this is written in Python (not R):

# While this course primarily uses RStudio, we found that scraping websites 
# especially those with dynamic JavaScript content and strict robot protections
# was significantly slower and less flexible in R. After exploring multiple R-based 
# solutions, we shifted to Python, which offered better tooling and much faster 
# performance for this kind of task.

# All of the data we collected is publicly available and the Fox News website does 
# not prohibit automated access to this content (robots are allowed). So while the 
# scraping itself is entirely legal, R’s available tools simply weren’t fast or 
# flexible enough to handle the scale we needed.

# Python supports asynchronous programming, and allowed us to download many articles 
# at once without waiting for each to finish sequentially.
# Furthermore, essential libraries like `curl_cffi` (for stealthy web requests), 
#`selenium_driverless` (for controlling a browser without launching a window), and 
#`stem` (for working with the Tor network) are only available in Python.

# ==============================================================================
# Install Libraries
# ==============================================================================

import asyncio
import json
import re
import time
import logging
from collections import Counter
from functools import wraps

import nltk
from nltk.tokenize import word_tokenize
import lxml.html
from stem import Signal
from stem.control import Controller

from selenium_driverless import webdriver
from selenium_driverless.scripts.network_interceptor import (
    NetworkInterceptor, InterceptedRequest, RequestPattern
)
from selenium_driverless.types.by import By
import curl_cffi.requests

# ==============================================================================
# CONFIGURATION
# ------------------------------------------------------------------------------
# These are the core values used across the script.
# HOST is the localhost IP used by the Tor proxy.
# TOR_PORT is the port used by SOCKS5 proxy for routing traffic anonymously.
# CONTROL_PORT is the port that lets us control Tor (e.g., request a new identity).
# URL is the starting point for scraping Fox News abortion politics section.
# API_BASE is the base URL for Fox News's internal article API.
# RED/RESET is used to color error logs red in the terminal.
# PATTERN_* are regex patterns to clean up JavaScript junk from article text.
# ==============================================================================

HOST = "127.0.0.1"
TOR_PORT = 9001
CONTROL_PORT = 8999

URL = "https://www.foxnews.com/category/politics/judiciary/abortion"
API_BASE = "https://www.foxnews.com/api/article-search?"

RED = "\033[91m"
RESET = "\033[0m"

PATTERN_IF = r'if\s*$[^)]*$'
PATTERN_BRACKETS = r'{[^}]*}'
PATTERN_ELSE = r'else\s*{[^}]*}'
PATTERN_IF_ELSE = r'if.*?}.*?else'

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
# TOR CONTROL
# ------------------------------------------------------------------------------
# This class wraps `stem` Tor controller logic for programmatic control.
# We use it to request new circuits (NEWNYM) to rotate identities.
# Useful for avoiding IP-based rate limits or bans.
# ==============================================================================

class AsyncTorController:
    def __init__(self, host='127.0.0.1', port=None):
        self.host = host
        self.port = port
        self.controller = None

    async def __aenter__(self):
        self.controller = await asyncio.to_thread(Controller.from_port, address=self.host, port=self.port)
        await asyncio.to_thread(self.controller.authenticate)
        return self

    async def __aexit__(self, *args):
        if self.controller:
            await asyncio.to_thread(self.controller.close)

    async def signal_newnym(self):
        await asyncio.to_thread(self.controller.signal, Signal.NEWNYM)
        print("TOR: Circuit refreshed.")

# ==============================================================================
# HTTP CLIENT
# ------------------------------------------------------------------------------
# curl_cffi is used here for stealthy, high-performance requests.
# It allows SOCKS5 proxy routing and Chrome user-agent impersonation.
# This helps avoid detection when accessing APIs at scale.
# ==============================================================================

class HttpClient:
    def __init__(self, proxy_port, impersonate, headers=None, params=None, timeout=30):
        self.client = curl_cffi.requests.AsyncSession(
            proxies={'http': f'socks5://{HOST}:{proxy_port}', 'https': f'socks5://{HOST}:{proxy_port}'},
            impersonate=impersonate,
            headers=headers or {},
            params=params or {},
            timeout=timeout,
            max_clients=10,
        )

    async def get(self, url):
        return await self.client.get(url)

    async def close(self):
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

# ==============================================================================
# DATA SHARING & TASK MANAGEMENT
# ------------------------------------------------------------------------------
# DataStore manages shared headers and cookies between browser and API fetches.
# TaskQueue lets us schedule async tasks and trigger NEWNYM after each.
# ==============================================================================

class DataStore:
    def __init__(self):
        self.data = {}
        self.lock = asyncio.Lock()
        self.queue = asyncio.Queue()

    async def update(self, other_dict):
        async with self.lock:
            self.data.update(other_dict)
            await self.queue.put(other_dict)

    async def wait_for_add(self):
        return await self.queue.get()

class TaskQueue:
    def __init__(self, controller: AsyncTorController):
        self.queue = asyncio.Queue()
        self.controller = controller

    async def put(self, task):
        await self.queue.put(task)
        await self.launch_task()

    async def launch_task(self):
        if not self.queue.empty():
            task = await self.queue.get()
            logger.error("New task started")
            await task()
            await self.queue.task_done()
            await self.controller.signal_newnym()

# ==============================================================================
# REQUEST INTERCEPTION
# ------------------------------------------------------------------------------
# This class intercepts Chrome browser network calls to extract headers.
# Required for copying request signature from browser to API requests.
# ==============================================================================

class InterceptRequest:
    def __init__(self, shared_dict: DataStore):
        self.shared_dict = shared_dict

    async def __call__(self, data: InterceptedRequest):
        if API_BASE in data.request.url and data.request.method == "GET":
            logger.info(f"Intercepted: {data.request.url}")
            await self.shared_dict.update({
                "url": data.request.url,
                "params": data.request.params,
                "headers": data.request.headers,
            })
        await data.continue_request(intercept_response=True)

# ==============================================================================
# HTML PARSER
# ------------------------------------------------------------------------------
# Parses article HTML body using XPath.
# Extracts: main article text, internal anchor links, image sources.
# ==============================================================================

class HtmlDataExtractor_IO:
    def __init__(self, html: str):
        self.tree = lxml.html.fromstring(html)

    def __call__(self):
        main = self.tree.xpath("//article[contains(@class, 'article-wrap')]")
        if not main:
            return "", [], []
        return (
            main[0].text_content(),
            [a.get("href") for a in main[0].xpath(".//a") if a.get("href")],
            [img.get("src") for img in main[0].xpath(".//img") if img.get("src")]
        )

# ==============================================================================
# UTILITIES
# ------------------------------------------------------------------------------
# Includes text cleaning, word counting, and data enrichment from raw HTML.
# ==============================================================================

async def clean_text(raw):
    text = re.sub(PATTERN_IF, '', raw, flags=re.DOTALL)
    text = re.sub(PATTERN_BRACKETS, '', text, flags=re.DOTALL)
    text = re.sub(PATTERN_ELSE, '', text, flags=re.DOTALL)
    text = re.sub(PATTERN_IF_ELSE, '', text, flags=re.DOTALL)
    return text.replace("\n", " ").replace("\xa0", " ")

async def create_word_count_dict(text):
    return dict(Counter([w.lower() for w in word_tokenize(text) if w.isalpha()]))

async def get_data(client: HttpClient, url: str, queue: TaskQueue, context: dict):
    try:
        res = await client.get(url)
        if res.status_code != 200:
            raise Exception("Rate limited")

        raw, links, imgs = HtmlDataExtractor_IO(res.text)()
        cleaned = await clean_text(raw)

        context.update({
            "text": cleaned,
            "urls": links,
            "imgs": imgs,
            "word_count": await create_word_count_dict(cleaned),
        })
        return context

    except Exception:
        await queue.put(lambda: None)
        await asyncio.sleep(1.0)
        return await get_data(client, url, queue, context)

# ==============================================================================
# SCRAPER ROUTINES
# ------------------------------------------------------------------------------
# API + full article fetchers. One gathers URLs, the other scrapes content.
# ==============================================================================

async def get_articles(data: list, queue: TaskQueue, **client_args):
    async with HttpClient(**client_args) as client:
        tasks = [
            get_data(client, f"https://www.foxnews.com{item['url']}", queue, item)
            for item in data if "https" not in item["url"]
        ]
        return await asyncio.gather(*tasks)

async def get_api(queue: TaskQueue, size=30, **client_args):
    async with HttpClient(**client_args) as client:
        tasks = [
            client.get(
                f"{API_BASE}searchBy=tags&values=fox-news%2Fpolitics%2Fjudiciary%2Fabortion"
                f"&excludeBy=tags&excludeValues=&size={size}&from={i*size}"
            ) for i in range(0, 151)
        ]
        responses = await asyncio.gather(*tasks)
        return [json.loads(r.text) for r in responses]

# ==============================================================================
# ORCHESTRATION
# ------------------------------------------------------------------------------
# Main glue logic: gets cookies/headers via Selenium, uses them to scrape API.
# ==============================================================================

async def datagatherer(store: DataStore, queue: TaskQueue):
    context = await store.wait_for_add()
    articles_raw = await get_api(queue, headers=context["headers"], params=context["params"],
                                 proxy_port=TOR_PORT, impersonate="chrome124")
    flattened = [item for sublist in articles_raw for item in sublist]
    enriched = await get_articles(flattened, queue,
                                   headers=context["headers"], params=context["params"],
                                   proxy_port=TOR_PORT, impersonate="chrome124")
    with open("data_articles_foxnews_full.json", "w") as f:
        json.dump(enriched, f, indent=2)
    print("Data saved to file.")

# ==============================================================================
# MAIN ENTRYPOINT
# ------------------------------------------------------------------------------
# Launches everything. Connects Selenium, sets proxy, triggers intercept.
# ==============================================================================

async def main():
    shared_data = DataStore()
    async with AsyncTorController(port=CONTROL_PORT) as controller:
        queue = TaskQueue(controller)
        options = webdriver.ChromeOptions()
        options.headless = True
        async with webdriver.Chrome(options=options) as driver:
            await driver.set_single_proxy(proxy="socks5://127.0.0.1:9001")
            async with NetworkInterceptor(driver, on_request=InterceptRequest(shared_data),
                                          patterns=[RequestPattern.AnyRequest]):
                await driver.get(URL, wait_load=True)
                await asyncio.gather(
                    datagatherer(shared_data, queue),
                    driver.sleep(5)
                )

# ==============================================================================

if __name__ == "__main__":
    nltk.download("punkt")
    print("Starting Fox News Scraper via Tor...")
    s = time.perf_counter()
    asyncio.run(main())
    e = time.perf_counter()
    print(f"Done in {e - s:.2f} seconds")

