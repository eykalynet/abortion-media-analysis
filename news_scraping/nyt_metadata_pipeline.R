# ==============================================================================
# Purpose: Scrape and extract abortion news articles from the New York Times 
# (2020–2024)
# Main Author: Erika Salvador
# Contributors: Maigan Lafontant, Emilie Ward
# Date modified: 2025-04-25
# ==============================================================================

# ==============================================================================
# ABOUT
# ==============================================================================

# This script collects articles related to abortion from the New York Times 
# Archive API for the years 2020 to 2024. It does the following:

#   - Connects to the NYT Archive API, which provides all articles published 
#     in a given month.
#   - Filters articles whose headline, snippet, or lead paragraph mention 
#     abortion (case-insensitive match).
#   - Extracts structured metadata including title, description, URL, 
#     publication date, category, authors, and article text.
#   - Respects the NYT API rate limit of 5 requests per minute by inserting 
#     a 12-second delay between requests.
#   - Allows for appending to a previous run (helpful in case of interruptions).
#   - Saves the final cleaned dataset as a CSV file.

# While our Fox News scraping pipeline was implemented in Python due to 
# JavaScript-heavy content and API interception needs, the New York Times 
# offers a well-documented public API that makes direct data access much simpler. 
# Thankfully, the NYT Archive API is freely available for registered users.
# Therefore, R was the natural and efficient choice for this task using httr 
# and tidyverse tools.

# All data retrieved is publicly available through the New York Times Archive API 
# under standard API use terms.

# ==============================================================================
# Install Libraries
# ==============================================================================

# Load required packages
library(httr)
library(jsonlite)
library(dplyr)
library(lubridate)
library(purrr)

# ==============================================================================
# CONFIGURATION
# ------------------------------------------------------------------------------
# This section defines core constants used across the scraper:
# nyt_key: API key used for authentication when connecting to the New York Times Archive API.
# save_path: Local file path where the cleaned article dataset will be saved as a CSV file.
# ==============================================================================

nyt_key <- "FdWOerQ3A95zaAvdCuRX1aRu4H0TyoiO"  # API key for NYT Archive access
save_path <- "../data/nyt_news_metadata_only.csv"       # Path to save the final output CSV

# ==============================================================================
# FETCHING FUNCTION
# ------------------------------------------------------------------------------
# Defines a helper function that handles the construction of the correct URL for 
# each year and month, submission of the GET request to the NYT Archive API, and 
# the parsing and filtering of the API response for abortion-related content. Then,
# finally, we structure the filtered output into clean fields for analysis
# The function also includes basic error handling by logging failed months 
# (important for debugging incomplete retrievals during long scrapes).
# ==============================================================================

# Function to fetch abortion-related articles for a given year and month
get_articles_by_month <- function(year, month) {
  # Build the API URL for the given year and month
  url <- paste0("https://api.nytimes.com/svc/archive/v1/", year, "/", month, ".json?api-key=", nyt_key)
  
  # Send GET request
  res <- GET(url)
  
  # If the request failed, log the issue (important for debugging failed months)
  if (status_code(res) != 200) {
    message("Request failed for ", year, "-", sprintf("%02d", month),
            " (Status code: ", status_code(res), ")")
    return(NULL)
  }
  
  # Parse the JSON response
  content_raw <- content(res, as = "text", encoding = "UTF-8")
  docs <- fromJSON(content_raw)$response$docs
  
  # Filter articles mentioning "abortion" in headline, snippet, or lead paragraph
  # Then extract structured fields
  docs |>
    filter(grepl("abortion", paste(headline$main, snippet, lead_paragraph), ignore.case = TRUE)) |>
    transmute(
      title = headline$main,                    # Main headline
      description = snippet,                    # Article snippet
      url = web_url,                             # Web URL of article
      publicationDate = substr(pub_date, 1, 10), # Publication date (YYYY-MM-DD)
      category = section_name,                   # News section (e.g., Politics, U.S.)
      authors = byline$original,                 # Author information
      text = ifelse(!is.na(lead_paragraph), lead_paragraph, snippet)  # Main text
    )
}


# ==============================================================================
# MAIN SCRIPT
# ------------------------------------------------------------------------------
# This section orchestrates the full scraping workflow. It generates a grid of all 
# year-month combinations from 2020 to 2024. It then checks if any previously saved 
# data exists, so the script can resume from partial runs without losing progress. 
# After establishing the timeline, the script iteratively requests abortion-related 
# articles month by month from the New York Times Archive API and inserts a 
# 12-second delay between each request to comply with the API's limit of five calls 
# per minute. Newly retrieved data are combined with any existing data to create a
# deduplicated dataset. Finally, the full curated dataset is saved as a structured 
# CSV file at the specified file path. 
# ==============================================================================

# Create a complete grid of year and month combinations from 2020 to 2024
date_grid <- expand.grid(year = 2020:2024, month = 1:12) |> arrange(year, month)

# Since the first full run was taking a long time, we added this step:
# If a previous output file exists, load it so we can resume and append new results
existing <- if (file.exists(save_path)) read.csv(save_path) else NULL

# Fetch articles month by month with a 12-second pause between API calls
nyt_abortion_articles <- map2_dfr(date_grid$year, date_grid$month, function(year, month) {
  Sys.sleep(12)  # Enforce NYT API limit: 5 requests per minute
  get_articles_by_month(year, month)  # Fetch abortion articles for the month
})

# If previous data exists, append new results to it
final_articles <- if (!is.null(existing)) bind_rows(existing, nyt_abortion_articles) else nyt_abortion_articles

# Save the final cleaned dataset to CSV
write_csv(final_articles, save_path)