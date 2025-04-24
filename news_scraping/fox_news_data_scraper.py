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

# ==============================================================================
# ASYNC TOR CONTROLLER
# ------------------------------------------------------------------------------
# This class provides asynchronous access to Tor's control interface using `stem`.
# It allows us to authenticate and manage a Tor controller instance, send NEWNYM 
# signals to request a new Tor identity (new circuit), send RELOAD signals to 
# refresh Tor configuration, and, lastly, use in an async context with automatic 
# setup/teardown
# ==============================================================================

class AsyncTorController:
    def __init__(self, host='127.0.0.1', port=None):
        self.host = host                 # Tor control host (usually localhost)
        self.port = port                 # Tor control port (e.g., 8999)
        self.controller = None           # Controller instance from `stem`

    async def __aenter__(self):
        """
        Connect and authenticate with the Tor controller when entering context.
        """
        try:
            self.controller = await asyncio.to_thread(
                Controller.from_port,
                address=self.host,
                port=self.port
            )
            if self.controller is None:
                raise RuntimeError("Failed to create a Tor controller.")
            await asyncio.to_thread(self.controller.authenticate)
            return self
        except Exception as e:
            print(f"Error during entering context: {e}")
            raise

    async def __aexit__(self, exc_type, exc, tb):
        """
        Cleanly close the controller connection when exiting context.
        """
        if self.controller is not None:
            await asyncio.to_thread(self.controller.close)
            self.controller = None
        else:
            print("Controller was not initialized, nothing to close.")

    async def signal_newnym(self):
        """
        Send a NEWNYM signal to Tor to request a new identity (rotate circuit).
        """
        if self.controller is not None:
            await asyncio.to_thread(self.controller.signal, Signal.NEWNYM)
            print("newnym received")
        else:
            print("Controller is not initialized.")

    async def reload(self):
        """
        Send a RELOAD signal to Tor to reload its configuration (e.g., torrc changes).
        """
        if self.controller is not None:
            await asyncio.to_thread(self.controller.signal, Signal.RELOAD)
            print("Reload signal received.")
        else:
            print("Controller is not initialized.")

# ==============================================================================
# TOR SIGNAL RATE LIMITER + TASK QUEUE
# ------------------------------------------------------------------------------
# `limit_nym_signals` prevents sending too many NEWNYM signals in a short time.
# `TaskQueue` manages tasks and rotates identity between them using NEWNYM.
# ==============================================================================

def limit_nym_signals(max_nym_signals: int = 1, time_period: int = 40):
    """
    Decorator that limits the number of times a NEWNYM signal can be sent
    within a specified time window to avoid overwhelming the Tor network.

    Parameters:
    - max_nym_signals (int): Maximum NEWNYM signals allowed.
    - time_period (int): Time window in seconds to enforce the limit.
    """
    def decorator(func):
        nym_signals = []              # List of timestamps when NEWNYM was called
        lock = asyncio.Lock()         # For thread-safe updates to timestamps

        @wraps(func)
        async def wrapper(*args, **kwargs):
            async with lock:
                current_time = asyncio.get_running_loop().time()
                # Keep only timestamps within the time window
                nym_signals[:] = [t for t in nym_signals if current_time - t < time_period]

                if len(nym_signals) < max_nym_signals:
                    result = await func(*args, **kwargs)
                    nym_signals.append(current_time)
                    return result
                else:
                    raise Exception("Max NEWNYM signals reached")

        return wrapper
    return decorator


class TaskQueue:
    """
    A simple task queue that executes asynchronous tasks one at a time.
    After each task, it sends a Tor NEWNYM signal to rotate identities.
    """

    def __init__(self, connector: AsyncTorController):
        self.queue = asyncio.Queue() # Async queue to hold tasks
        self.connector = connector  # Tor controller to manage identity rotation

    async def put(self, task):
        """
        Add a new coroutine task to the queue and trigger execution.
        """
        await self.queue.put(task)
        await self.launch_task()

    # You can uncomment the decorator below to activate rate limiting on NEWNYM.
    # Personally, we did not find the need to do it since we did not scrape
    # articles since 2009. 
    
    # @limit_nym_signals(max_nym_signals=3, time_period=20)
    async def send_nym_signal(self):
        """
        Send a NEWNYM signal to request a new Tor identity.
        """
        await self.connector.signal_newnym()

    async def launch_task(self):
        """
        Run the next task in the queue and rotate Tor identity afterwards.
        """
        if not self.queue.empty():
            try:
                task = await self.queue.get()
                logger.error("New task detected")
                await task()
                await self.queue.task_done()
            except Exception as e:
                # Fail silently
                pass

# ==============================================================================
# HTML DATA EXTRACTOR
# ------------------------------------------------------------------------------
# This class parses the HTML content of an article using lxml.
# It extracts cleaned text from <p> tags within the article body (excluding 
# <a><strong>), all internal anchor links inside the article container, and 
# all image sources from the same article container
# Designed to be used like a function: extractor() returns (text, links, images)
# ==============================================================================

class HtmlDataExtractor_IO:
    def __init__(self, html: str) -> None:
        """
        Initialize the HTML parser and validate input type.
        """
        if not isinstance(html, str):
            raise ValueError("Invalid input type: Expected string containing HTML.")

        # Create lxml HTML tree with error recovery mode enabled
        self.tree = lxml.html.fromstring(html, parser=lxml.html.HTMLParser(recover=True))

    def __call__(self, xpath: str = "//article[contains(@class, 'article-wrap')]") -> list:
        """
        Extract article content, anchor links, and image sources from the given HTML.
        Parameters:
        - xpath: The XPath root for searching <a> and <img> elements (defaults to article-wrap container)
        
        Returns:
        - Tuple of (cleaned article text, list of links, list of image URLs)
        """
        try:
            # 1. Main text: extract <p> tags under .article-body, skip <p> with <a><strong>
            text = ' '.join([
                p.text_content()
                for p in self.tree.xpath("//div[@class='article-body']/p")
                if not p.xpath("./a/strong")
            ])

            # 2. Extract all internal anchor links under the provided article XPath
            links = [
                a.get('href')
                for a in self.tree.xpath(f"{xpath}//a")
                if a.get('href') is not None
            ]

            # 3. Extract all image URLs under the same article XPath
            images = [
                img.get('src')
                for img in self.tree.xpath(f"{xpath}//img")
                if img.get('src') is not None
            ]

            return text, links, images

        except lxml.etree.XPathError as e:
            raise ValueError(f"Invalid XPath expression: {e}")
        except lxml.etree.XMLSyntaxError as e:
            raise ValueError(f"XML syntax error: {e}")
        except lxml.etree.ParserError as e:
            raise ValueError(f"XML parser error: {e}")
        except Exception as e:
            raise ValueError(f"An unexpected error occurred: {e}")

    def __del__(self):
        """
        Clean up the lxml tree object.
        """
        try:
            del self.tree
        except AttributeError:
            pass

# ==============================================================================
# CUSTOM EXCEPTION
# ------------------------------------------------------------------------------
# This exception is raised when a request is blocked or throttled by the server
# (e.g., non-200 response due to scraping limits or IP-based restrictions).
# It can be caught and handled to trigger a Tor identity rotation (NEWNYM).
# ==============================================================================

class RateLimit(Exception):
    """Custom exception raised when rate limiting is detected."""
    pass
  
# ==============================================================================
# DATA FETCHING + CLEANING UTILITIES
# ------------------------------------------------------------------------------
# These functions handle article text extraction and cleanup, word frequency 
# analysis, and resilient GET requests with Tor circuit rotation on failure
# ==============================================================================

async def create_word_count_dict(text):
    """
    Create a dictionary of lowercase word frequencies from the given text.
    Filters out non-alphabetic tokens.
    """
    return dict(Counter([word.lower() for word in word_tokenize(text) if word.isalpha()]))

async def extract_text(html):
    """
    Use the HtmlDataExtractor_IO class to extract:
    - Main body text
    - Internal links
    - Image sources
    """
    try:
        return HtmlDataExtractor_IO(html=html)()
    except Exception as e:
        print(e, "in filtering")

async def get_data(c: HttpClient, url: str, task_queue: TaskQueue, dict_DATA=None, 
sleep: float = 0.5) -> curl_cffi.requests.Response:
    """
    Fetch a URL using the provided HttpClient and parse the result if dict_DATA is provided.

    - If the request fails or returns a non-200 status, a RateLimit is raised.
    - On success, extracts and cleans HTML content, populates dict_DATA with:
        - 'text': cleaned article body
        - 'urls': internal anchor links
        - 'imgs': image sources
        - 'world count' [sic]: frequency of each word

    On failure, it triggers a NEWNYM signal via TaskQueue and retries the request.
    """
    try:
        response = await c.get(url)
        if response.status_code != 200:
            logger.error(f"{response.status_code}       {url}")
            raise RateLimit()

        logger.info(f"{response.status_code}              {url}")

        if dict_DATA is not None:
            # Extract HTML content
            html_r, html_u, iimg = await extract_text(response.text)

            # Clean unwanted JS logic from the article body
            pattern_if = r'if\s*$[^)]*$'
            pattern_brackets = r'{[^}]*}'
            pattern_else = r'else\s*{[^}]*}'
            pattern = r'if.*?}.*?else'

            clean_text = re.sub(pattern_if, '', html_r, flags=re.DOTALL)
            clean_text = re.sub(pattern_brackets, '', clean_text, flags=re.DOTALL)
            clean_text = re.sub(pattern_else, '', clean_text, flags=re.DOTALL)
            new_text = re.sub(pattern, '', clean_text, flags=re.DOTALL)

            clean_text1 = re.sub(r'[\n\xa0]', '', new_text)
            clean_text = re.sub(r'\s+', ' ', clean_text1)

            # Populate the metadata dictionary
            dict_DATA["text"] = clean_text
            dict_DATA["urls"] = html_u
            dict_DATA["imgs"] = iimg
            dict_DATA["world count"] = await create_word_count_dict(new_text)  

            return dict_DATA

        return response

    except Exception as e:
        print("err in get_data:", e, url)
        try:
            # Trigger a NEWNYM and retry the request
            await asyncio.create_task(task_queue.put(task_queue.send_nym_signal))
            await asyncio.sleep(sleep)
            return await asyncio.create_task(get_data(c=c, 
          task_queue=task_queue, url=url, dict_DATA=dict_DATA))
        except Exception as e:
            print("Critical failure in get_data retry loop:", e)


# ==============================================================================
# ARTICLE + API FETCHERS
# ------------------------------------------------------------------------------
# These functions retrieve lists of article metadata or full content, use 
# HttpClient through Tor, and coordinate with the TaskQueue for identity rotation
# ==============================================================================

async def get_articles(data: list[dict], task_queue: TaskQueue, **args) -> list[dict]:
    """
    Given a list of article metadata (from API), fetch full article content.
    
    Parameters:
    - data: list of dictionaries with 'url' keys
    - task_queue: used for circuit rotation if rate-limited
    - args: forwarded to HttpClient (e.g., proxy_port, headers)

    Returns:
    - A list of enriched article dictionaries with full text, links, images, and word counts
    """
    print(data) 

    async with HttpClient(**args) as client:
        try:
            return await asyncio.gather(*[
                asyncio.create_task(
                    get_data(
                        task_queue=task_queue,
                        url=f"https://www.foxnews.com{article['url']}",
                        c=client,
                        dict_DATA=article
                    )
                )
                for article in data if "https" not in article['url']
            ])
        except Exception as e:
            print("Error in get_articles:", e)


async def get_api(task_queue: TaskQueue, size: int = 30, **args):
    """
    Fetch raw article metadata from the Fox News abortion-related article API.
    
    Parameters:
    - task_queue: used for identity rotation on failure
    - size: number of articles per API call (pagination)
    - args: forwarded to HttpClient (e.g., proxy_port, headers)

    Returns:
    - A list of response objects from the API
    """
    try:
        async with HttpClient(**args) as client:
            return await asyncio.gather(*[
                asyncio.create_task(
                    get_data(
                        task_queue=task_queue,
                        url=(
                            f"https://www.foxnews.com/api/article-search"
                            f"?searchBy=tags&values=fox-news%2Fpolitics%2Fjudiciary%2Fabortion"
                            f"&excludeBy=tags&excludeValues=&size={size}&from={30*i}"
                            f"&mediaTags=politics%7Cjudiciary%7Cabortion"
                        ),
                        c=client
                    )
                )
                for i in range(0, 151)  # 151 * 30 = 4530 results (our last run got
                # 3,000 results) 
            ])
    except Exception as e:
        print("Error in get_api:", e)
        
# ==============================================================================
# DATA GATHERER
# ------------------------------------------------------------------------------
# This function waits for intercepted request headers/params from the browser,
# calls the internal Fox News API using those headers, collects article metadata 
# and then fetches full article content, and saves the enriched results (text, 
# images, word count) to ../data/fox_news_data.json
# ==============================================================================

async def datagatherer(shared_aces_walues: DataStore, task_queue: TaskQueue):
    """
    Orchestrates API scraping using captured headers/params and saves full article content.

    Parameters:
    - shared_aces_walues: DataStore instance containing captured request data
    - task_queue: TaskQueue for identity rotation and retry management
    """
    data = await asyncio.create_task(shared_aces_walues.wait_for_add())
    print("#" * 100, "\nCaptured headers and params:\n", data, "\n", "#" * 100)

    try:
        # Step 1: Call Fox News internal article API using captured headers
        res1 = await asyncio.create_task(get_api(
            task_queue=task_queue,
            params=data["params"],
            headers=data["headers"],
            proxy_port=9001,
            impersonate="chrome124"
        ))
        print(f"Number of API responses: {len(res1)}")

        # Step 2: Flatten and parse JSON article metadata
        dd = [item for sublist in [json.loads(res.text) for res in res1] for item in sublist]
        print(f"Total articles to fetch: {len(dd)}")

        # Step 3: Fetch and enrich each article with full content
        res2 = await asyncio.create_task(get_articles(
            data=dd,
            task_quete=task_queue, 
            params=data["params"],
            headers=data["headers"],
            proxy_port=9001,
            impersonate="chrome124"
        ))
        print(f"Fetched and enriched articles: {len(res2)}")

        # Step 4: Save the results to ../data/fox_news_data.json
        with open("../data/fox_news_data.json", "a", encoding="utf-8") as f:
            json.dump(res2, f, ensure_ascii=False, indent=2)

        print("#" * 100)
        print("Sample article:", res2[0])  # Preview the first article
        print("#" * 100)

    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}")
    except Exception as e:
        print("Error during data gathering:", e)

# ==============================================================================
# SELENIUM INTERACTIONS (Driverless)
# ------------------------------------------------------------------------------
# These helper functions automate user actions. It locates elements via XPath, 
# simulates a click, and triggers the API call by interacting with the "Load 
# More" button
# ==============================================================================

async def get_elem(driver: webdriver, xpath: str, timeout: float = 10) -> WebElement:
    """
    Locate a single DOM element using XPath within a given timeout period.
    
    Parameters:
    - driver: active SeleniumDriverless browser instance
    - xpath: XPath string to locate the element
    - timeout: how long to wait before timing out

    Returns:
    - WebElement: the element found
    """
    return await driver.find_element(By.XPATH, xpath, timeout=timeout)


async def click(elem: WebElement) -> None:
    """
    Click the given element, moving the virtual cursor to it first (helps with visibility checks).
    """
    await elem.click(move_to=True)


async def trigger_API_call(driver: webdriver, xpath: str = "//div[@class = 'button load-more js-load-more']/a"):
    """
    Attempts to click the 'Load More' button on the article page to initiate
    the network call that loads abortion-related articles.

    Parameters:
    - driver: active SeleniumDriverless browser instance
    - xpath: XPath of the load more button (default matches known Fox layout)
    """
    try:
        e = await get_elem(driver=driver, xpath=xpath)
        await click(elem=e)
        logger.info("Triggered initial API call via Load More button")
    except Exception as e:
        logger.error(f"Error triggering API call: {e}")

# ==============================================================================
# MAIN EXECUTION FUNCTION
# ------------------------------------------------------------------------------
# This function initializes a Tor controller (to rotate identities), a headless 
# SeleniumDriverless browser instance, a network interceptor to capture API 
# request headers, then loads the Fox News abortion page, triggers the internal 
# API call via simulated click, and launches data extraction using captured headers
# ==============================================================================

async def main(proxy: str = None):
    # Setup browser options
    options = webdriver.ChromeOptions()
    options.user_data_dir = "Profile 3"  # Use a separate browser profile
    options.headless = True              # Run without UI

    shared_access_values = DataStore()   # Shared headers/params from intercepted request

    # Launch Tor controller for circuit rotation
    async with AsyncTorController(port=8999) as ATC:
        task_queue = TaskQueue(connector=ATC)

        # Launch headless Chrome with large WebSocket buffer
        async with webdriver.Chrome(max_ws_size=2 ** 30, options=options) as driver:
            if proxy is not None:
                await driver.set_single_proxy(proxy=proxy)

            # Attach network interceptor to capture API headers and params
            async with NetworkInterceptor(
                driver,
                on_request=InterceptRequest(shared_dict=shared_access_values),
                patterns=[RequestPattern.AnyRequest],
                intercept_auth=True
            ) as interceptor:
                try:
                    logger.info("Chrome started + Network interceptor initialized.")

                    # Load the Fox News abortion politics page
                    await asyncio.ensure_future(driver.get(url=URL, wait_load=True))
                    logger.info("Chrome loaded target URL.")

                    # Simulate Load More button click + begin data scraping
                    await asyncio.gather(*[
                        asyncio.create_task(f()) for f in [
                            lambda: datagatherer(shared_aces_walues=shared_access_values, task_queue=task_queue),
                            lambda: trigger_API_call(driver=driver)
                        ]
                    ])

                    await driver.quit()  # Cleanup driver after work completes

                except Exception as e:
                    logger.error(f"Error in main: {e}")
                except KeyboardInterrupt:
                    await driver.quit()

# ==============================================================================
# ENTRY POINT
# ------------------------------------------------------------------------------
# This block runs the async main() function if the script is executed directly.
# It uses a local Tor SOCKS5 proxy and measures execution time.
# ==============================================================================

if __name__ == "__main__":
    s = time.perf_counter()  # Start timing
    asyncio.run(main(proxy="socks5://127.0.0.1:9001"))
    e = time.perf_counter()  # End timing

    execution_time = e - s
    print(f"Execution time: {execution_time:.2f} seconds")

