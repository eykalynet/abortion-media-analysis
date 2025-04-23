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

# --- Standard Library ---
import asyncio
import json
import logging
import re
import time
from collections import Counter
from functools import wraps

# --- NLP ---
import nltk
from nltk.tokenize import word_tokenize

# --- HTML Parsing ---
import lxml.html

# --- Tor (via stem) ---
from stem import Signal
from stem.control import Controller

# --- Web Scraping and Automation ---
import curl_cffi.requests
from selenium_driverless import webdriver
from selenium_driverless.scripts.network_interceptor import (
    InterceptedRequest, NetworkInterceptor, Request, RequestPattern, RequestStages
)
from selenium_driverless.types.by import By
from selenium.webdriver.remote.webelement import WebElement

# --- WebSocket Communication Errors ---
from cdp_socket.exceptions import CDPError

# ==============================================================================
# CONFIGURATION
# ------------------------------------------------------------------------------
# This section defines core constants used across the scraper:
# BASH_SCRIPT: Command used to launch a custom Tor instance with specific ports 
# and U.S. exit nodes.
# HOST: The localhost IP address where the Tor proxy runs.
# RED / RESET: Terminal color codes for formatting error messages in red.
# POST_URL_CONTENT_mesages: Base URL for Fox News's internal search API (used to 
# extract abortion articles).
# URL: The main page for judiciary-related abortion news on the Fox News website.
# ==============================================================================

BASH_SCRIPT = """   tor --ControlPort 8999 --SocksPolicy "accept 127.0.0.1"    --SocksPort 9001 --ExitNodes "{us}"   """
HOST = "127.0.0.1"
RED = "\033[91m"
RESET = "\033[0m"
POST_URL_CONTENT_mesages = "https://www.foxnews.com/api/article-search?" 

print(POST_URL_CONTENT_mesages)

URL = 'https://www.foxnews.com/category/politics/judiciary/abortion'

# ==============================================================================
# LOGGING CONFIGURATION
# ------------------------------------------------------------------------------
# This setup customizes terminal logging behavior to improve visibility.
# Errors are highlighted in red using ANSI escape codes.
# Logs include level, logger name, and message.
# ==============================================================================

class ColoredFormatter(logging.Formatter):
    """
    Custom log formatter that highlights ERROR-level messages in red.
    """
    def format(self, record):
        if record.levelno == logging.ERROR:
            # Add red color to error messages
            record.msg = f"{RED}{record.msg}{RESET}"
        elif record.levelno == logging.INFO:
            # INFO messages are left unstyled
            record.msg = f"{record.msg}"
        return super().format(record)

# Create a logger for this module
logger = logging.getLogger(__name__)

# StreamHandler directs logs to the terminal/console
handler = logging.StreamHandler()

# Use the custom formatter for colored output
formatter = ColoredFormatter('%(levelname)s - %(name)s - %(message)s')
handler.setFormatter(formatter)

# Attach the handler to the logger
logger.addHandler(handler)

# Set the minimum log level to INFO
logger.setLevel(logging.INFO)

# ==============================================================================
# HTTP CLIENT
# ------------------------------------------------------------------------------
# This class wraps curl_cffi's AsyncSession to send async HTTP(S) requests
# through a SOCKS5 proxy (Tor), impersonate a real browser user-agent (e.g., 
# Chrome), and support use in an async context (with async `with`)
# ==============================================================================

class HttpClient:
    def __init__(self, proxy_port: int, impersonate: str, 
                  headers: dict, params: dict, 
                  timeout: float = 30.0):
        self.timeout = timeout

        # Initialize an async HTTP session with Tor routing and impersonation
        self.client = curl_cffi.requests.AsyncSession(
            max_clients = 10,            # Limit concurrent requests
            timeout = self.timeout,      # Request timeout
            http_version = 1,            # Use HTTP/1.1
            allow_redirects = True,      # Follow redirects
            max_redirects = 10,          # Max allowed redirects
            proxies = {                  # SOCKS5 proxy through Tor
                'http': f'socks5://{HOST}:{proxy_port}',
                'https': f'socks5://{HOST}:{proxy_port}'
            },
            impersonate = impersonate,   # Browser impersonation (e.g., chrome124)
            headers = headers or {},     # Custom headers
            params = params or {}        # Query parameters
        )

    async def get(self, url: str) -> curl_cffi.requests.Response:
        """
        Asynchronously send a GET request to the specified URL.
        """
        return await self.client.get(url)

    async def post(self, url: str, **kawrgs) -> curl_cffi.requests.Response:
        """
        Asynchronously send a POST request to the specified URL with optional kwargs.
        """
        return await self.client.post(url, **kawrgs)

    async def close(self):
        """
        Close the asynchronous HTTP session.
        """
        await self.client.close()

    async def __aenter__(self) -> 'HttpClient':
        """
        Enable use of this class in an async context manager (`async with`).
        """
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        Ensure session is closed on exiting the async context manager.
        """
        await self.close()
        
# ==============================================================================
# DATASTORE
# ------------------------------------------------------------------------------
# This class handle a shared dictionary (`self.data`) to store headers, cookies, 
# etc., an asyncio queue to pass updates to dependent components, and thread-safe 
# access using asyncio locks
# ==============================================================================

class DataStore:
    def __init__(self):
        self.data = {}                    # Internal shared dictionary
        self.lock = asyncio.Lock()        # Lock for safe concurrent access
        self.queue = asyncio.Queue()      # Queue to notify when data is updated

    async def get(self, key):
        """
        Safely retrieve a value by key from the shared dictionary.
        """
        async with self.lock:
            return self.data.get(key)

    async def delete(self, key):
        """
        Safely delete a key-value pair from the shared dictionary.
        """
        async with self.lock:
            if key in self.data:
                del self.data[key]

    async def update(self, other_dict):
        """
        Safely update the shared dictionary and notify via the queue.
        """
        async with self.lock:
            self.data.update(other_dict)         # Merge in new values
            await self.queue.put(other_dict)     # Notify listeners

    async def wait_for_add(self):
        """
        Wait for and retrieve the next update from the queue.
        """
        return await self.queue.get()

# ==============================================================================
# INTERCEPT REQUEST
# ------------------------------------------------------------------------------
# This class is passed to `NetworkInterceptor` and handles intercepted browser
# requests. When the Fox News API endpoint is detected, it logs request details 
# (URL, method, headers, params), saves them into the shared DataStore, and 
# continues the request to allow page loading
# ==============================================================================

class InterceptRequest:
    def __init__(self, shared_dict: DataStore):
        self.shared_dict = shared_dict  # Shared storage for intercepted request data

    async def __call__(self, data: InterceptedRequest):
        try:
            # Always continue the request so there is response interception
            await data.continue_request(
                url=data.request.url,
                intercept_response=True
            )

            # Filter for the specific Fox News API request we care about
            if POST_URL_CONTENT_mesages in data.request.url and data.request.method == "GET":
                logger.info(
                    f"\nurl:\n{data.request.url}\n"
                    f"method\n{data.request.method}\n"
                    f"headers\n{data.request.headers}\n"
                    f"params\n{data.request.params}"
                )

                # Update shared data store with request metadata
                await self.shared_dict.update({
                    "url": data.request.url,
                    "params": data.request.params,
                    "headers": data.request.headers
                })

                print("data adet ")

                # Optionally intercept response (e.g., to debug)
                await data.continue_request(url=None, intercept_response=True)

        except Exception as e:
            logger.error(f"Failed to get data: {e}")

# ==============================================================================
# TOR LAUNCHER
# ------------------------------------------------------------------------------
# This class allows us to programmatically launch a Tor process using a custom
# bash command. It can be used with an async context manager (`async with`)
# to ensure that Tor runs during scraping and terminates cleanly afterward.
# ==============================================================================

class TorLauncher:
    def __init__(self, bash_script: str):
        self.bash_script = bash_script  # Bash command to launch Tor
        self.process = None             # Reference to the subprocess

    async def __aenter__(self):
        """
        Start the Tor process asynchronously when entering the context.
        """
        self.process = await asyncio.create_subprocess_shell(cmd=self.bash_script)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        Guarantee the Tor process is terminated when exiting the context.
        Handles both graceful shutdown and forced termination if needed.
        """
        if self.process:
            try:
                await self.process.wait()  # Wait for Tor to finish (usually never)
            except Exception as e:
                print(f"Error waiting for process: {e}")
                try:
                    self.process.kill()  # Attempt to kill process if wait fails
                except Exception as e:
                    print(f"Error killing process: {e}")
        return False  # Prevents suppressing any exception that occurred

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
    with open("../data/fox_news_data.json", "w") as f::
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
                await driver.get(URL, wait_load=True, timeout = 90)
                await asyncio.gather(
                    datagatherer(shared_data, queue),
                    driver.sleep(5)
                )


# ==============================================================================
# EXECUTION WRAPPER
# ==============================================================================

if __name__ == "__main__":
    nltk.download("punkt")
    print("Starting Fox News Scraper via Tor...")
    s = time.perf_counter()
    asyncio.run(main())
    e = time.perf_counter()
    print(f"Done in {e - s:.2f} seconds")

