# ==============================================================================
# Purpose: Scrape and extract data from the White House Presidential Actions page
# Main Author: Erika Salvador
# Contributors: Maigan Lafontant, Emilie Ward
# Date modified: 2025-03-25
# ==============================================================================

# Load libraries
library(robotstxt)
library(rvest)
library(httr)
library(jsonlite)
library(dplyr)
library(stringr)
library(lubridate)
library(purrr)

# ==============================================================================
# Check scraping permissions
# ==============================================================================

paths_allowed("https://www.foxnews.com/?msockid=1f6d91ae1b076b4d3b0d85791a9a6afe")
paths_allowed("https://www.msnbc.com/")

# ==============================================================================
# Load SerpApi key securely
# ==============================================================================

api_key <- Sys.getenv("SERPAPI_KEY")
if (api_key == "") stop("Missing API key! Please set SERPAPI_KEY in your .Renviron file.")

  # Check README.md for set-up instructions

# ==============================================================================
# Define Queries and News Sites
# ==============================================================================

queries <- c("abortion", "abortion rights", "roe v. wade", "planned parenthood", "pro-life")
sites <- c("msnbc.com", "foxnews.com")

# ==============================================================================
# Function: get_abortion_news_links()
# ------------------------------------------------------------------------------
# Since Fox News and MSNBC do not offer public APIs to search or retrieve
# historical articles, we rely on SerpApi to access Google Search results
# filtered by:
#   Site domain (e.g., site:msnbc.com)
#   Abortion-related queries (e.g., "abortion rights", "roe v. wade")
#   Date range (2009–2024)
#
# SerpApi provides a stable and legal way to access Google Search results
# through an API and supports pagination (10 results per page). This function
# loops through each (query, site) pair and collects titles and links for 
# up to `pages * 10` results per pair.
#
# Output:
# A de-duplicated tibble with columns:
#   - query (e.g., "abortion")
#   - source (e.g., "foxnews.com")
#   - page (page number in pagination)
#   - title (article title from search results)
#   - url (direct link to the article)
# ==============================================================================

get_abortion_news_links <- function(queries, sites, pages = 10, api_key) {
  all_results <- tibble()
  
  for (query in queries) {
    for (site in sites) {
      cat("Searching:", query, "on", site, "\n") # to display progress on console 
      
      for (i in 0:(pages - 1)) {
        start_idx <- i * 10  # pagination offset for Google results
        
        res <- GET(
          url = "https://serpapi.com/search.json",
          query = list(
            engine = "google",
            q = paste("site:", site, query),  # e.g., site:foxnews.com abortion
            start = start_idx,
            api_key = api_key,
            tbs = "cdr:1,cd_min:1/1/2009,cd_max:12/31/2024"  # custom date range filter
          )
        )
        
        if (res$status_code != 200) next  # skip if failed request
        
        results <- tryCatch(fromJSON(content(res, "text", encoding = "UTF-8")), 
                            error = function(e) NULL)
        if (is.null(results) || !"organic_results" %in% names(results)) next
        
        links <- results$organic_results$link
        titles <- results$organic_results$title
        
        df <- tibble(
          query = query,
          source = site,
          page = i + 1,
          title = titles,
          url = links
        )
        
        all_results <- bind_rows(all_results, df)
        Sys.sleep(runif(1, 1.2, 2))  # polite delay to avoid being rate-limited
      }
    }
  }
  
  return(distinct(all_results))  # remove duplicates across all results
}