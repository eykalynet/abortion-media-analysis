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
# Core constants used throughout the script:
# BASH_SCRIPT: Command to launch a Tor instance with specific ports and U.S. 
# exit nodes
# HOST: Localhost IP used for SOCKS5 proxy routing
# RED / RESET: Terminal ANSI escape codes for colorized error logging
# POST_URL_CONTENT_MESSAGES: Substring to identify intercepted NBC API requests
# URL: Target NBC News politics page for abortion-related news
# ==============================================================================

BASH_SCRIPT = """tor --ControlPort 8999 --SocksPolicy "accept 127.0.0.1" --SocksPort 9001 --ExitNodes "{us}" """
HOST = "127.0.0.1"
RED = "\033[91m"      
RESET = "\033[0m"    
POST_URL_CONTENT_MESSAGES = "www.nbcnews.com/politics/" 

print(POST_URL_CONTENT_MESSAGES)

URL = "https://www.nbcnews.com/politics/abortion-news" 

# ==============================================================================
# LOGGING CONFIGURATION
# ------------------------------------------------------------------------------
# This configures the logging behavior for the script. It uses a custom formatter 
# to highlight error messages in red. Logs include the log level, module name, 
# and the message itself.
# ==============================================================================

class ColoredFormatter(logging.Formatter):
    """
    Custom log formatter to colorize ERROR messages in red.
    """
    def format(self, record):
        if record.levelno == logging.ERROR:
            record.msg = f"{RED}{record.msg}{RESET}"  # Apply red color
        elif record.levelno == logging.INFO:
            record.msg = f"{record.msg}"              # Leave INFO unchanged
        return super().format(record)

# Initialize the logger for the current module
logger = logging.getLogger(__name__)

# Create a stream handler to output logs to the console
handler = logging.StreamHandler()

# Apply the custom formatter to the handler
formatter = ColoredFormatter('%(levelname)s - %(name)s - %(message)s')
handler.setFormatter(formatter)

# Attach the handler to the logger
logger.addHandler(handler)

# Set the minimum log level to INFO 
logger.setLevel(logging.INFO)

# ==============================================================================
# HTML DATA EXTRACTOR (NBC)
# ------------------------------------------------------------------------------
# This class is designed to extract article links from dynamically rendered
# HTML pages on NBC News using lxml. It targets `div` blocks styled for article
# teasers within the politics section.
# ==============================================================================

class HtmlDataExtractor_IO:
    def __init__(self, html: str) -> None:
        """
        Initialize the parser and validate input.
        """
        if not isinstance(html, str):
            raise ValueError("Invalid input type: expected HTML string.")

        # Parse the HTML using a forgiving parser to handle malformed content
        self.tree = lxml.html.fromstring(html, parser=lxml.html.HTMLParser(recover=True))

    def __call__(self, xpath: str = "//script[@id='__NEXT_DATA__']") -> list[str]:
        """
        Extracts internal article links from the NBC news listing layout.

        Returns:
        - A list of href URLs from teaser blocks within politics section.

        Note:
        - Ignores None values and only returns valid `href` attributes.
        """
        try:
            return [
                a.get('href')
                for a in self.tree.xpath(
                    '//div[contains(@class, "styles_itemsContainer")]'
                    '//div[contains(@class, "wide-tease-item__wrapper ")]'
                    '//div[contains(@class, "wide-tease-item__info-wrapper")]/a'
                )
                if a.get('href') is not None
            ]
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
        Clean up the lxml tree reference on deletion.
        """
        try:
            del self.tree
        except AttributeError:
            pass
          
# ==============================================================================
# DATASTORE: Shared Asynchronous Dictionary
# ------------------------------------------------------------------------------
# This class serves as a shared data store for managing request metadata
# (like headers and params) captured from browser interception.
# It supports thread-safe updates using asyncio locks and an internal queue to 
# notify other components of new additions
# ==============================================================================

class DataStore:
    def __init__(self):
        self.data = {}                    # Internal shared dictionary
        self.lock = asyncio.Lock()        # Lock to ensure safe concurrent access
        self.queue = asyncio.Queue()      # Queue to signal when new data is added

    async def get(self, key):
        """
        Safely retrieve a value from the store by key.
        """
        async with self.lock:
            return self.data.get(key)

    async def delete(self, key):
        """
        Safely delete a key-value pair from the store.
        """
        async with self.lock:
            if key in self.data:
                del self.data[key]

    async def update(self, other_dict):
        """
        Safely update the store with a new dictionary and notify listeners via the queue.
        """
        async with self.lock:
            self.data.update(other_dict)
            await self.queue.put(other_dict)  # Push the update into the queue

    async def wait_for_add(self):
        """
        Wait for and retrieve the next added dictionary from the queue.
        """
        return await self.queue.get()
      
# ==============================================================================
# INTERCEPT REQUEST HANDLER
# ------------------------------------------------------------------------------
# This callable class is used with NetworkInterceptor.
# It listens for matching request URLs (e.g., API calls to NBC or Fox), captures 
# headers and parameters, and updates a shared DataStore for downstream scraping 
# functions
# ==============================================================================

class InterceptRequest:
    def __init__(self, shared_dict: DataStore):
        """
        Initialize with a shared DataStore instance to store intercepted data.
        """
        self.shared_dict = shared_dict

    async def __call__(self, data: InterceptedRequest):
        """
        Handle intercepted requests:
        - If the URL contains the configured pattern (e.g., NBC API),
          extract headers and params and update the shared data store.
        - Continue the request so the page can still load normally.
        """
        try:
            # Continue the request and allow response interception
            await data.continue_request(
                url=data.request.url,
                intercept_response=True
            )

            # Check if the request URL matches our target API endpoint
            if POST_URL_CONTENT_mesages in data.request.url:
                # Optionally log request info for debugging
                # logger.info(f"Intercepted: {data.request.url}")

                # Save the intercepted metadata to shared storage
                await self.shared_dict.update({
                    "url": data.request.url,
                    "params": data.request.params,
                    "headers": data.request.headers
                })

                print("data adet")  # Debug print — replace with logger if needed

                # Continue the request again for completeness (may be redundant)
                await data.continue_request(url=None, intercept_response=True)

        except Exception as e:
            logger.error(f"Failed to get data: {e}")

# ==============================================================================
# TASK QUEUE + SELENIUM HELPERS
# ------------------------------------------------------------------------------
# TaskQueue is a simple async queue for deferring and retrieving coroutine tasks.
# get_elem finds a WebElement on the page using XPath.
# click, well, as the name suggests, clicks on the WebElement with cursor 
# movement to simulate human behavior.
# ==============================================================================

class TaskQueue:
    """
    Lightweight asynchronous queue for managing coroutine tasks.
    """
    def __init__(self):
        self.queue = asyncio.Queue()  # Internal task queue

    async def put(self, task):
        """
        Add a coroutine task to the queue.
        """
        await self.queue.put(task)

    async def get(self):
        """
        Retrieve the next task from the queue.
        """
        return await self.queue.get()


async def get_elem(driver: webdriver, xpath: str, timeout: float = 10) -> WebElement:
    """
    Locate a DOM element on the page using the provided XPath.
    
    Parameters:
    - driver: An active SeleniumDriverless instance
    - xpath: XPath string to locate the element
    - timeout: Max time to wait for the element to appear

    Returns:
    - WebElement if found within the timeout
    """
    return await driver.find_element(By.XPATH, xpath, timeout=timeout)


async def click(elem: WebElement) -> None:
    """
    Click the given WebElement. Moves cursor to the element before clicking
    to simulate more human-like interaction.
    """
    await elem.click(move_to=True)
  
# ==============================================================================
# NBCNEWS SCRAPER (ASYNCHTMLSESSION + TOR PROXY)
# ------------------------------------------------------------------------------
# This class uses `requests_html.AsyncHTMLSession` to asynchronously fetch
# pages from NBC News via a SOCKS5 proxy (Tor). It supports asynchronous URL 
# fetching with proxy support and context-managed session lifecycle
# ==============================================================================

class NBCNewsScraper:
    def __init__(self):
        """
        Initialize an asynchronous HTML session for scraping.
        """
        self.session = AsyncHTMLSession()

    async def fetch_url(self, url):
        """
        Fetch a given URL asynchronously using the Tor proxy.

        Parameters:
        - url (str): The webpage URL to fetch

        Returns:
        - response: The HTML response from the server
        """
        proxies = {
            'http': 'socks5://127.0.0.1:9001',
            'https': 'socks5://127.0.0.1:9001'
        }
        response = await self.session.get(url, proxies=proxies)
        return response

    async def run(self, url_list):
        """
        Fetch a list of URLs concurrently.

        Parameters:
        - url_list (list[str]): A list of webpage URLs to fetch

        Returns:
        - list of responses
        """
        tasks = [self.fetch_url(url) for url in url_list]
        return await asyncio.gather(*tasks)

    async def __aenter__(self):
        """
        Enable use of `async with` for resource management.
        """
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        Close the asynchronous session on exit.
        """
        await self.session.close()


# ==============================================================================
# ARTICLE FORMATTER + WORD COUNT HELPER
# ------------------------------------------------------------------------------
# `create_word_count_dict` tokenizes text and counts word frequencies
# format_adat` cleans scraped article content
# ==============================================================================

# Global store 
data_ola = []

async def create_word_count_dict(text: str) -> dict:
    """
    Create a frequency dictionary of all alphabetic words in the given text.

    Parameters:
    - text (str): The input text to tokenize and count

    Returns:
    - dict: Word frequency dictionary
    """
    return dict(Counter([word.lower() for word in word_tokenize(text) if word.isalpha()]))

async def format_adat(dd: dict) -> dict:
    """
    Format and enrich raw JSON response from NBC News API or page scrape.

    Extracts:
    - Main article text
    - Word frequency dictionary
    - Cleans line breaks from the text

    Parameters:
    - dd (dict): Nested article dictionary from scraped data

    Returns:
    - dict: Enriched article data with cleaned text and word count
    """
    data = dd["props"]["initialState"]["article"]["content"][0]

    # Extract and join raw paragraph segments
    txt = data["content"]["text"]
    text_c = ''.join([s for s in txt])

    # Add word count dictionary
    data["world_count"] = await create_word_count_dict(text_c)

    # Clean line breaks
    clean_text = re.sub(r'[\n\r]', '', text_c)
    data["content"]["text"] = clean_text

    return data


# ==============================================================================
# NBC NEWS DATA GATHERER
# ------------------------------------------------------------------------------
# This function waits for captured headers/params from browser interception, 
# uses `NBCNewsScraper` to fetch full article pages through Tor, extracts and 
# cleans article content using `format_adat`, then accumulates results into the 
# global `data_ola` list
# ==============================================================================

async def datagatherer(cookies, task_queue: TaskQueue, shared_aces_walues: DataStore):
    """
    Gather and process NBC News articles using previously captured headers and URLs.

    Parameters:
    - cookies: Placeholder for session continuity (currently unused)
    - task_queue: Queue of URL lists to fetch
    - shared_aces_walues: DataStore with headers/params from network interception
    """
    data = await asyncio.create_task(shared_aces_walues.wait_for_add())
    logger.error(data)  # Debug log of captured metadata

    async with NBCNewsScraper() as s:
        while True:
            try:
                task = await task_queue.get()

                if "exit" in task:
                    break  # Exit loop gracefully if signaled

                print(f"Fetched task with {len(task)} URLs")

                # Fetch article pages concurrently through Tor proxy
                responses = await s.run(task)
                print([r.status_code for r in responses])

                # Parse embedded JSON from each page and format content
                parsed_articles = [
                    await format_adat(json.loads(
                        lxml.html.fromstring(r.html.html)
                        .xpath('//script[@id="__NEXT_DATA__"]/text()')[0]
                    )) for r in responses
                ]

                print(parsed_articles)
                data_ola.extend(parsed_articles)  # Add parsed articles to global list

            except Exception as e:
                print(f"Error in datagatherer loop: {e}")
                
# ==============================================================================
# LOAD ARTICLE LINKS (NBC INTERFACE)
# ------------------------------------------------------------------------------
# This function: clicks "Load More" on NBC News repeatedly to reveal more 
# articles, extracts article links after each click using HtmlDataExtractor_IO, 
# deduplicates new links before pushing them to a task queue, and signals 
# completion by sending an "exit" message to the queue
# ==============================================================================

async def load_articles_links(task_queue: TaskQueue, driver: webdriver, xpath: str = "//button[contains(@class, 'animated-ghost-button')]"):
    """
    Iteratively clicks the "Load More" button on NBC News and collects article links.

    Parameters:
    - task_queue: A TaskQueue instance to push new article URL lists
    - driver: A SeleniumDriverless instance
    - xpath: The XPath to the "Load More" button
    """
    list2 = []  # Tracks previously seen links to avoid duplicates

    for _ in range(8):  # Repeat click-extract process 8 times
        try:
            # Find and click the "Load More" button
            e = await get_elem(driver=driver, xpath=xpath)
            await driver.sleep(1)
            await click(elem=e)
            logger.info("Triggered Load More button")

            # Extract article links after the click
            data = HtmlDataExtractor_IO(await driver.page_source)()

            # Only push new links not already seen
            if list2:
                new_links = list(set(data) - set(list2))
                await task_queue.put(new_links)
            else:
                await task_queue.put(data)

            list2 = data  # Update seen links
            print(f"Collected {len(data)} links")

        except Exception as e:
            logger.error(f"Error during Load More interaction: {e}")

    # Signal completion to downstream consumers
    await task_queue.put("exit")
    
# ==============================================================================
# MAIN ENTRYPOINT (NBC NEWS SCRAPER)
# ------------------------------------------------------------------------------
# This function launches a headless SeleniumDriverless browser, sets up a 
# network interceptor to capture request headers, loads the NBC News politics/
# abortion page, clicks "Load More" to reveal articles and collect links, and 
# launches a scraping pipeline that extracts and saves cleaned content
# ==============================================================================

async def main(proxy: str = None):
    # Setup ChromeDriverless browser options
    options = webdriver.ChromeOptions()
    options.user_data_dir = "Profile 3"  # Uses a persistent profile 
    options.headless = True              # Runs browser in headless mode

    # Shared resources for coordination
    shared_access_values = DataStore()
    task_queue = TaskQueue()

    async with webdriver.Chrome(max_ws_size=2 ** 30, options=options) as driver:
        if proxy is not None:
            await driver.set_single_proxy(proxy=proxy)

        async with NetworkInterceptor(
            driver,
            on_request=InterceptRequest(shared_dict=shared_access_values),
            patterns=[RequestPattern.AnyRequest],
            intercept_auth=True
        ) as interceptor:
            try:
                # Step 1: Navigate to the NBC politics page
                await asyncio.ensure_future(driver.get(url=URL, wait_load=True))

                # Step 2: Capture session cookies
                cookies = await driver.get_cookies()
                print("Cookies:", cookies)

                # Step 3: Concurrently load links and extract article content
                await asyncio.gather(*[
                    asyncio.create_task(f()) for f in [
                        lambda: load_articles_links(task_queue=task_queue, driver=driver),
                        lambda: datagatherer(cookies=cookies, task_queue=task_queue, shared_aces_walues=shared_access_values)
                    ]
                ])

                print("Data scraping completed. Saving to file...")

                # Step 4: Save results to file
                with open("../data/nbc_news_data.json", "a", encoding="utf-8") as f:
                    json.dump(data_ola, f, ensure_ascii=False, indent=2)

            except Exception as e:
                logger.error(f"Error in main: {e}")
            except KeyboardInterrupt:
                await driver.quit()


# ==============================================================================
# ENTRY POINT
# ------------------------------------------------------------------------------
# Launches the main scraper using Tor proxy 
# ==============================================================================

if __name__ == "__main__":
    asyncio.run(main("socks5://127.0.0.1:9001"))


