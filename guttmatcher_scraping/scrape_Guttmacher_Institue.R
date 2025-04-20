# ==============================================================================
# Purpose: Scrape and extract data Guttmacher Institute Page
# Main Author: Emilie
# Contributors: Maigan Lafontant, Erika Salvador
# Date modified: 2025-03-25
# ==============================================================================

# Load libraries
library(rvest)
library(robotstxt)
library(dplyr)
library(stringr)
library(lubridate)
library(purrr)
library(RSelenium)
library(progress)
library(readr)
library(tidyr)

# ==============================================================================
# Check scraping permissions
# ==============================================================================
paths_allowed("https://www.guttmacher.org/state-legislation-tracker")
#returned true for scraping

# ==============================================================================
# HELPER FUNCTION: Automatically find a free port for RSelenium
# ==============================================================================
find_free_port <- function(start = 4567L, max_tries = 20) {
  for (port in start:(start + max_tries)) {
    con <- try(suppressWarnings(socketConnection("localhost", 
                                                 port = port, 
                                                 server = TRUE, 
                                                 blocking = TRUE, 
                                                 open = "r+")), 
               silent = TRUE)
    if (!inherits(con, "try-error")) {
      close(con)
      return(port)
    }
  }
  stop("No free port found after trying", max_tries, "ports.")
}

# ==============================================================================
# Scraping Guttmatcher Legistlation Tracker
# ==============================================================================

scrape_article_ids_to_txt <- function(
    url = "https://www.guttmacher.org/state-legislation-tracker",
    output_id_file = "article_ids.csv",
    output_table_file = "policy_table.csv",
    port = 4567L
) {
  # Start RSelenium
  rD <- rsDriver(browser = "firefox", port = port, verbose = FALSE)
  remDr <- rD$client
  remDr$navigate(url)
  Sys.sleep(10)
  
  # Get rendered page source
  page_source <- remDr$getPageSource()[[1]]
  page_url <- read_html(page_source)
  
  policy_articles <- page_url %>%
    html_element("#policy-policies") %>%
    html_elements("article")
  
  # Extract article IDs
  article_data <- map(policy_articles, function(article) {
    article_id <- html_attr(article, "id")
    tables <- html_elements(article, "table")  # ✅ Corrected this line
    
    if (length(tables) == 0) return(NULL)
    
    parsed_tables <- html_table(tables, fill = TRUE)
    
    tables_with_id <- lapply(parsed_tables, function(tbl) {
      tbl$article_id <- article_id
      return(tbl)
    })
    
    return(tables_with_id)
  })
  
  # Combine wrangle and clean
  article_data_clean <- compact(article_data)
  combined_table <- bind_rows(unlist(article_data_clean, recursive = FALSE)) |> 
    select(-X3) |>
    pivot_wider(
      names_from = X1,
      values_from = X2
    )
  
  # Save to file
  write_csv(combined_table, "policy_articles_tables.csv")
  
  # Clean up
  remDr$close()
  rD$server$stop()
  
  # message(length(article_id), " article IDs written to ", output_id_file)
  # message("Policy table written to ", output_table_file)
  
  return(invisible(combined_table))
}

#running functions
scrape_article_ids_to_txt(
  output_id_file = "/Users/emilieward/Desktop/guttmacher_ids.csv",
  
)

