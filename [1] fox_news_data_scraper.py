# ==============================================================================
# Purpose: Scrape and extract abortion news data from Fox News (2019-2025)
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
# ABOUT
# ==============================================================================
