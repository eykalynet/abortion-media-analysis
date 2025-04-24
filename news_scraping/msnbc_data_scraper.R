# ==============================================================================
# Purpose: Scrape and extract abortion news data from MSNBC (2020–2024)
# Main Author: Erika Salvador
# Contributors: Maigan Lafontant, Emilie Ward
# Date modified: 2025-03-25
# ==============================================================================

# ==============================================================================
# ABOUT
# ==============================================================================

# This script collects abortion-related news articles from MSNBC between 2020–2024
# using Google News via SerpAPI and visits each article to extract full content.
# It does the following:
#
#   - Sends queries to SerpAPI with a custom date range and site restriction
#     (e.g., "abortion site:msnbc.com" from 2020 to 2024)
#   - Extracts metadata like article titles, publication dates, and snippets
#   - Visits each article and scrapes the full text, author, and publication date
#   - Saves the cleaned results in a structured JSON format
#
# Unlike our Fox News scraping pipeline, which relied on Tor, browserless drivers,
# and internal API interception (which R struggled to support), this MSNBC scraper
# uses SerpAPI, which returns clean metadata and direct links with no dynamic JS
# or bot protections.
#
# Thus, we found R to be sufficient and appropriate for this more straightforward task.
#
# Note:
# Our original Python-based MSNBC scraper, which tried to use browser automation to
# repeatedly click the "Load More" button on the MSNBC website, unfortunately failed
# to retrieve the complete set of articles. After investigating, we found that the
# website itself does not load the full archive (even with manual interaction).
# Moreover, MSNBC does not maintain a category or topic archive page specifically
# for abortion. Because of this limitation, we decided to switch to using Google News
# via SerpAPI, which allowed us to reliably retrieve abortion-related content
# across the 2020–2024 period.

# ==============================================================================
# Load Required Libraries
# ==============================================================================

library(httr)
library(jsonlite)
library(rvest)
library(dplyr)
library(purrr)
library(stringr)

# ==============================================================================
# SerpAPI Query for MSNBC Articles (Paginated)
# ==============================================================================

serpapi_key <- "2ea2160e89eed6f4d6ca61c30b2cb397ae81c904a8b5e8422d072bb6f3bdc9ad"
base_url <- "https://serpapi.com/search"

all_results <- list()

for (start in seq(0, 4900, by = 100)) {
  cat("Querying offset:", start, "\n")
  
  params <- list(
    q = "abortion site:msnbc.com",
    tbm = "nws",
    api_key = serpapi_key,
    tbs = "cdr:1,cd_min:1/1/2020,cd_max:12/31/2024",
    num = 100,
    start = start
  )
  
  res <- GET(base_url, query = params)
  json <- content(res, as = "parsed", simplifyVector = FALSE)
  if (is.null(json$news_results)) break
  
  all_results <- append(all_results, json$news_results)
  Sys.sleep(2)
}

articles <- all_results

# ==============================================================================
# Helper Function to Extract Full Text and Metadata
# ==============================================================================

extract_article <- function(url) {
  tryCatch({
    page <- read_html(url)
    title <- page |> html_element("h1") |> html_text(trim = TRUE)
    text <- page |> html_elements("article p") |> html_text(trim = TRUE) |> paste(collapse = " ")
    authors <- page |> html_element(xpath = "//*[contains(@class,'author')]") |> html_text(trim = TRUE)
    pub_date <- page |> html_element("meta[property='article:published_time']") |> html_attr("content")
    
    list(
      title = title %||% "",
      authors = authors %||% "",
      url = url,
      description = "",
      text = text %||% "",
      pub_date = pub_date %||% ""
    )
  }, error = function(e) {
    message(sprintf("Failed on: %s", url))
    list(
      title = "",
      authors = "",
      url = url,
      description = "",
      text = "",
      pub_date = ""
    )
  })
}

# ==============================================================================
# Scrape Article Text and Metadata for Each Result
# ==============================================================================

output <- map(articles, function(article) {
  url <- article$link
  cat("Processing:", url, "
")
  info <- extract_article(url)
  info$description <- article$snippet %||% ""
  Sys.sleep(2)
  info
}) |> 
  map(~ .x[c("title", "authors", "url", "description", "text", "pub_date")])

# ==============================================================================
# Save Results to JSON
# ==============================================================================

write_json(output, "../data/nbc_news_data.json", pretty = TRUE, auto_unbox = TRUE)
