# ==============================================================================
# Purpose: Scrape and extract abortion news data from The New York Times (2020-2024)
# Main Author: Erika Salvador
# Contributors: Maigan Lafontant, Emilie Ward
# Date modified: 2025-04-25
# ==============================================================================

# ==============================================================================
# ABOUT
# ==============================================================================
#
# This script collects news articles related to abortion from The New York Times 
# between January 2020 and December 2024. It performs the following steps:
#
#   - Routes all web traffic through the Tor network, anonymizing the scraping 
#     process by bouncing requests through volunteer-run servers worldwide.
#   - Connects via a local SOCKS5 proxy to securely tunnel all HTTP and HTTPS 
#     traffic through Tor.
#   - Captures internal API requests made by the NYT search page to retrieve 
#     authentic headers, parameters, and access points.
#   - Sends direct GraphQL queries to The New York Times’s internal API to 
#     gather structured article metadata (titles, publication dates, and URLs).
#   - Fetches each article’s full text by visiting the individual article pages.
#   - Compiles and saves the complete dataset including metadata and article text  
#     into a clean, structured JSON file for analysis.
# 
# # All of the data we collected is publicly available and the Fox News website does 
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
import aiofiles
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
# POST_URL_CONTENT_messages: Base URL for The New York Times internal GraphQL 
# API endpoint (used to extract abortion-related articles).
# URL: The main page for abortion-related news search results on the NYT website.
# ==============================================================================
 
BASH_SCRIPT = """tor --ControlPort 8999 --SocksPolicy "accept 127.0.0.1" --SocksPort 9001 --ExitNodes "{us}" """
HOST = "127.0.0.1"
RED = "\033[91m"
RESET = "\033[0m"
POST_URL_CONTENT_messages = "https://samizdat-graphql.nytimes.com/graphql/v2?operationName=SearchRootQuery&variable"

URL = (
    "https://www.nytimes.com/search?dropmab=false"
    "&endDate=2024-12-29"
    "&lang=en"
    "&query=abortion"
    "&sort=oldest"
    "&startDate=2022-05-04"
)

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
    INFO messages are left unstyled.
    """
    def format(self, record):
        if record.levelno == logging.ERROR:
            # Add red color to error messages
            record.msg = f"{RED}{record.msg}{RESET}"
        elif record.levelno == logging.INFO:
            # INFO messages are left plain
            record.msg = f"{record.msg}"
        return super().format(record)

# Create a logger for this module
logger = logging.getLogger(__name__)

# StreamHandler directs logs to the terminal/console
handler = logging.StreamHandler()

# Apply the custom ColoredFormatter to the handler
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
# through a SOCKS5 proxy (Tor), impersonate a real browser user-agent (e.g., Chrome),
# and support use in an async context (with async `with`).
# ==============================================================================

class HttpClient:
    def __init__(self, proxy_port: int, impersonate: str, headers: dict, params: dict, timeout: float = 30.0):
        """
        Initialize an async HTTP session with Tor routing and browser impersonation.
        """
        self.timeout = timeout

        self.client = curl_cffi.requests.AsyncSession(
            max_clients=10,                   # Limit concurrent requests
            timeout=self.timeout,              # Request timeout
            http_version=1,                    # Use HTTP/1.1
            allow_redirects=True,              # Follow redirects
            max_redirects=10,                  # Max allowed redirects
            proxies={                          # SOCKS5 proxy through Tor
                'http': f'socks5://{HOST}:{proxy_port}',
                'https': f'socks5://{HOST}:{proxy_port}'
            },
            impersonate=impersonate,            # Browser impersonation (e.g., chrome124)
            headers=headers or {},              # Custom headers
            params=params or {}                 # Query parameters
        )

    async def get(self, url: str) -> curl_cffi.requests.Response:
        """
        Asynchronously send a GET request to the specified URL.
        """
        return await self.client.get(url)

    async def post(self, url: str, **kwargs) -> curl_cffi.requests.Response:
        """
        Asynchronously send a POST request to the specified URL with optional kwargs.
        """
        return await self.client.post(url, **kwargs)

    async def close(self):
        """
        Close the asynchronous HTTP session.
        """
        await self.client.close()

    async def __aenter__(self) -> 'HttpClient':
        """
        Enable use of this class as an async context manager.
        """
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        Ensure the session is closed when exiting the async context.
        """
        await self.close()

# ==============================================================================
# DATASTORE
# ------------------------------------------------------------------------------
# This class handles a shared dictionary (`self.data`) to store headers, cookies,
# etc., an asyncio queue to pass updates to dependent components, and thread-safe 
# access using asyncio locks.
# ==============================================================================

class DataStore:
    def __init__(self):
        """
        Initialize an empty shared dictionary, a lock for concurrent access,
        and a queue to notify listeners about updates.
        """
        self.data = {}                 # Internal shared dictionary
        self.lock = asyncio.Lock()      # Lock for safe concurrent access
        self.queue = asyncio.Queue()    # Queue to notify when data is updated

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
            self.data.update(other_dict)       # Merge in new values
            await self.queue.put(other_dict)    # Notify listeners

    async def wait_for_add(self):
        """
        Wait for and retrieve the next update from the queue.
        """
        return await self.queue.get()

# ==============================================================================
# INTERCEPT REQUEST
# ------------------------------------------------------------------------------
# This class is passed to `NetworkInterceptor` and handles intercepted browser
# requests. When the New York Times search API endpoint is detected, it logs 
# request details (URL, method, headers, params), saves them into the shared 
# DataStore, and continues the request to allow page loading.
# ==============================================================================

class InterceptRequest:
    def __init__(self, shared_dict: DataStore):
        """
        Initialize with a shared DataStore for saving intercepted request data.
        """
        self.shared_dict = shared_dict  # Shared storage for intercepted request metadata

    async def __call__(self, data: InterceptedRequest):
        """
        Handle an intercepted browser request, save metadata if relevant, and continue the request.
        """
        try:
            # Always continue the request to allow page load and intercept the response
            await data.continue_request(
                url=data.request.url,
                intercept_response=True
            )

            # Filter for the specific NYT API request we care about
            if POST_URL_CONTENT_messages in data.request.url and data.request.method == "GET":
                logger.info(
                    f"\nurl:\n{data.request.url}\n"
                    f"method:\n{data.request.method}\n"
                    f"headers:\n{data.request.headers}\n"
                    f"params:\n{data.request.params}"
                )

                # Update shared data store with request metadata
                await self.shared_dict.update({
                    "url": data.request.url,
                    "params": data.request.params,
                    "headers": data.request.headers
                })

                print("data adet")  # (Optional) Quick debug print

                # Optionally intercept response body (e.g., for debugging)
                await data.continue_request(url=None, intercept_response=True)

        except Exception as e:
            logger.error(f"Failed to intercept and process request: {e}")

# ==============================================================================
# TOR LAUNCHER
# ------------------------------------------------------------------------------
# This class allows us to programmatically launch a Tor process using a custom 
# bash command. It can be used with an async context manager (`async with`) to 
# ensure that Tor runs during scraping and terminates cleanly afterward.
# ==============================================================================

class TorLauncher:
    def __init__(self, bash_script: str):
        """
        Initialize the TorLauncher with a custom bash script to launch Tor.
        """
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
                    self.process.kill()    # Attempt to kill process if wait fails
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
# setup/teardown.
# ==============================================================================

class AsyncTorController:
    def __init__(self, host='127.0.0.1', port=None):
        """
        Initialize the Tor controller with a host address and control port.
        """
        self.host = host            # Tor control host (usually localhost)
        self.port = port            # Tor control port (e.g., 8999)
        self.controller = None      # Controller instance from `stem`

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

    async def __aexit__(self, exc_type, exc_val, exc_tb):
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
    - max_nym_signals (int): Maximum NEWNYM signals allowed within the time window.
    - time_period (int): Time window in seconds to enforce the limit.
    """
    def decorator(func):
        nym_signals = []             # List of timestamps when NEWNYM was called
        lock = asyncio.Lock()         # Lock for thread-safe updates

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
        self.queue = asyncio.Queue()   # Async queue to hold tasks
        self.connector = connector     # Tor controller to manage identity rotation

    async def put(self, task):
        """
        Add a new coroutine task to the queue and trigger execution.
        """
        await self.queue.put(task)
        await self.launch_task()

    # Optionally decorate with @limit_nym_signals if needed
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
# It extracts cleaned text from <p> tags inside articles, skipping certain 
# decorative tags like <a><strong>. It is designed to be used like a function:
# extractor() returns cleaned text from the article body.
# ==============================================================================

class HtmlDataExtractor_IO:
    def __init__(self, html: str) -> None:
        """
        Initialize the HTML parser and validate the input type.
        """
        if not isinstance(html, str):
            raise ValueError("Invalid input type: Expected string containing HTML.")

        # Create lxml HTML tree with error recovery mode enabled
        self.tree = lxml.html.fromstring(html, parser=lxml.html.HTMLParser(recover=True))

    def __call__(self, xpath: str = "//div[contains(@class, 'intro svelte-')]/p | //p[contains(@class, 'css-at') or contains(@class, 'body') or contains(@class, 'g-text')]") -> str:
        """
        Extract article body text based on the provided XPath.
        
        Parameters:
        - xpath: XPath expression for locating text-containing <p> tags (default matches NYT structures).

        Returns:
        - Cleaned article text as a single concatenated string.
        """
        try:
            return ' '.join([p.text_content() for p in self.tree.xpath(xpath)])
        
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
# This exception is raised when a request is blocked or throttled by the server.
# It triggers circuit rotation via Tor.
# ==============================================================================

class RateLimit(Exception):
    """Custom exception raised when rate limiting is detected."""
    pass


# ==============================================================================
# DATA FETCHING + CLEANING UTILITIES
# ------------------------------------------------------------------------------
# Functions for safely fetching URLs, writing to files, and cleaning text.
# ==============================================================================

async def write_to_file(file_name: str, dict_data: dict):
    """
    Asynchronously append a dictionary to a JSON file.
    """
    try:
        async with sem:
            async with aiofiles.open(file_name, mode='r+') as file:
                file_content = await file.read()
                data = json.loads(file_content) if file_content else []
                data.append(dict_data)
                await file.seek(0)
                await file.write(json.dumps(data, ensure_ascii=False, indent=2))
                await file.truncate()
    except (FileNotFoundError, json.JSONDecodeError):
        async with sem:
            async with aiofiles.open(file_name, mode='w') as file:
                await file.write(json.dumps([dict_data], ensure_ascii=False, indent=2))
    except Exception as e:
        logger.error(f"Error writing to file: {e}")


async def get_data(c: HttpClient, url: str, task_queue: TaskQueue, sleep: float = 0.5, max_tries: int = 10) -> curl_cffi.requests.Response:
    """
    Fetch a URL with retries and Tor circuit rotation on failure.
    """
    tries = 0
    while True:
        try:
            response = await c.get(url)
            if response.status_code != 200:
                logger.error(f"{response.status_code} {url}")
                raise RateLimit()
            logger.info(f"{response.status_code} {url}")
            return response
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            tries += 1
            if tries >= max_tries:
                raise Exception(f"Failed to get data after {max_tries} tries")
            await task_queue.put(task_queue.send_nym_signal)
            await asyncio.sleep(sleep)


async def create_word_count_dict(text: str) -> dict:
    """
    Count word frequencies from article text.
    """
    return dict(Counter(word.lower() for word in word_tokenize(text) if word.isalpha()))


# ==============================================================================
# ARTICLE + API FETCHERS
# ------------------------------------------------------------------------------
# Functions to scrape article full texts and fetch API search metadata.
# ==============================================================================

async def get_article_data(dict_art: dict, task_queue: TaskQueue, client: HttpClient) -> dict:
    """
    Retrieve and parse a single article's full text and word count.
    """
    try:
        res = await get_data(task_queue=task_queue, url=dict_art["url"], c=client)
        text = HtmlDataExtractor_IO(res.text)()
        dict_art["text"] = text
        dict_art["word_count"] = await create_word_count_dict(text=text)
        return dict_art
    except Exception as e:
        logger.error(f"Error fetching article data: {e}")


async def get_api(urls: list, task_queue: TaskQueue, client: HttpClient):
    """
    Concurrently fetch multiple API pages.
    """
    try:
        return await asyncio.gather(*[
            asyncio.create_task(get_data(task_queue=task_queue, url=url, c=client))
            for url in urls
        ])
    except Exception as e:
        logger.error(f"Error fetching API pages: {e}")


# ==============================================================================
# DATA GATHERER
# ------------------------------------------------------------------------------
# Main function that drives article metadata retrieval and article full text scraping.
# ==============================================================================

async def datagaderer(shared_aces_walues: DataStore, task_queue: TaskQueue):
    data = await shared_aces_walues.wait_for_add()
    cursor_value = "YXJyYXljb25uZWN0aW9uOjE5"
    offset_value = 5

    async with HttpClient(params=data["params"], headers=data["headers"], proxy_port=9001, impersonate="chrome124") as client:
        for _ in range(10000):
            try:
                res_list = await get_api([format_url(cursor_value, offset_value)], task_queue, client)
                for res in res_list:
                    if res.status_code != 200:
                        continue
                    articles_batch = [
                        data_formater(node["node"])
                        for node in json.loads(res.text)["data"]["search"]["hits"]["edges"]
                    ]
                    full_articles = await asyncio.gather(*[
                        get_article_data(article, task_queue, client)
                        for article in articles_batch
                    ])
                    await write_to_file("../data/nyt_news_data.json", full_articles)

                    page_info = json.loads(res.text)["data"]["search"]["hits"]["pageInfo"]
                    cursor_value = page_info['endCursor']

            except Exception as e:
                logger.error(f"Error during data gathering loop: {e}")


# ==============================================================================
# SELENIUM INTERACTIONS (Driverless)
# ------------------------------------------------------------------------------
# Helper functions for interacting with dynamic browser elements (scrolling, clicks).
# ==============================================================================

async def get_elem(driver: webdriver, xpath: str, timeout: float = 10) -> WebElement:
    return await driver.find_element(By.XPATH, xpath, timeout=timeout)


async def click(elem: WebElement) -> None:
    await elem.click(move_to=True)


async def trigger_API_call(driver: webdriver, xpath: str = "//div[@class='css-1r5gb7s']/button"):
    try:
        e = await get_elem(driver, xpath)
        await click(elem=e)
        logger.info("Triggered initial API call via Load More button")
    except Exception as e:
        logger.error(f"Error triggering API call: {e}")


# ==============================================================================
# MAIN EXECUTION FUNCTION
# ------------------------------------------------------------------------------
# This function launches everything and handles Tor routing + Selenium driving.
# ==============================================================================

async def main(proxy: str = None):
    options = webdriver.ChromeOptions()
    options.user_data_dir = "Profile 3"
    options.headless = False

    shared_access_values = DataStore()

    async with AsyncTorController(port=8999) as ATC:
        task_queue = TaskQueue(connector=ATC)

        async with webdriver.Chrome(max_ws_size=2**30, options=options) as driver:
            if proxy is not None:
                await driver.set_single_proxy(proxy=proxy)

            async with NetworkInterceptor(
                driver,
                on_request=InterceptRequest(shared_dict=shared_access_values),
                patterns=[RequestPattern.AnyRequest],
                intercept_auth=True
            ) as interceptor:
                try:
                    logger.info("Chrome launched + NetworkInterceptor initialized.")

                    await driver.get(url=URL, wait_load=True)
                    await driver.sleep(4)
                    await driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

                    await asyncio.gather(
                        asyncio.create_task(datagaderer(
                            shared_aces_walues=shared_access_values,
                            task_queue=task_queue
                        ))
                    )

                    await driver.sleep(3600)

                except Exception as e:
                    logger.error(f"Error in main execution: {e}")
                except KeyboardInterrupt:
                    logger.info("KeyboardInterrupt received. Shutting down.")
                finally:
                    await driver.quit()


# ==============================================================================
# ENTRY POINT
# ------------------------------------------------------------------------------
# Executes the main() function if the script is run directly.
# ==============================================================================

if __name__ == "__main__":
    s = time.perf_counter()
    asyncio.run(main(proxy="socks5://127.0.0.1:9001"))
    e = time.perf_counter()

    execution_time = e - s
    print(f"Execution time: {execution_time:.2f} seconds")
