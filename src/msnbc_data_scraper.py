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


