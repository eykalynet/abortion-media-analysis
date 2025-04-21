# ==============================================================================
# Purpose: Scrape and extract data Guttmacher Institute Page
# Main Author: Emilie Ward
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
find_free_port <- function(start = 4565L, max_tries = 20) {
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
    port = 4565L
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
# ==============================================================================
# Scraping Guttmatcher Legistlation Tracker For Text in Links
# ==============================================================================

scrape_article_links_and_texts <- function(
    url= "https://www.guttmacher.org/state-legislation-tracker",
    output_id_file = "article_ids.csv",
    output_table_file = "policy_table.csv",
) {
  # Start RSelenium
  
  rD <- rsDriver(browser = "firefox", port = port, verbose = FALSE)
  remDr <- rD$client
  remDr$navigate(url)
  Sys.sleep(10)
  # Get rendered page source
  page_source <- remDr$getPageSource()[[1]]
  page_url <- read_html(page_source)
  
  articles <- page_url %>%
    html_element("#policy-policies") %>%
    html_elements("article")
  
  article_links <- map_dfr(articles, function(article) {
    article_id <- html_attr(article, "id")
    
    # Find all links within this article
    links <- html_elements(article, "a.policy-link")
    
    if (length(links) == 0) {
      return(tibble(
        article_id = article_id,
        link_text = NA_character_,
        policy_link = NA_character_
      ))
    }
    
    # Extract href and text for each link
    map_dfr(links, function(link) {
      link_text <- html_text2(link)
      href <- html_attr(link, "href")
      full_link <- paste0("https://www.guttmacher.org", href)
      
      tibble(
        article_id = article_id,
        link_text = link_text,
        policy_link = full_link
      )
    })
  })
  
  remDr$close()
  rD$server$stop()
  
  return(article_links)
}

article_link_table <- scrape_article_links_and_texts()
write_csv(article_link_table, "/Users/emilieward/Desktop/guttmacher_article_links.csv")

# ==============================================================================
# Scraping Guttmatcher Legistlation Tracker For State Wide Data Tables
# ==============================================================================

scrape_tables <- function(url, output_dir = "/Users/emilieward/Desktop/DS_data/", 
                          port = 4573L
) {
  #creating the directory to save the files to
  if (!dir.exists(output_dir)) dir.create(output_dir, recursive = TRUE)
  
  # Start RSelenium
  rD <- rsDriver(browser = "firefox", port = port, verbose = FALSE)
  remDr <- rD$client
  remDr$navigate(url)
  Sys.sleep(10)
  
  # Get rendered page source
  page_source <- remDr$getPageSource()[[1]]
  page_url <- read_html(page_source)
  
  
  sections <- html_elements(page_url, ".WordSection1")
  tables <- lapply(sections, function(section) {
    html_nodes(section, "table#slp-table") %>%
      lapply(html_table, fill = TRUE)
  })
  
  # Flatten the list of lists
  tables_flat <- do.call(c, tables)
  
  # Combine all tables into one data frame
  combined <- bind_rows(tables_flat)
  
  # Create a safe file name from the URL
  clean_name <- str_replace_all(url, "[^a-zA-Z0-9]", "_")
  output_file <- file.path(output_dir, paste0(clean_name, ".csv"))
  
  # Save to CSV
  write_csv(combined, output_file)
  
  message("Saved: ", output_file)
  
  remDr$close()
  rD$server$stop()
  
  return(combined)
}

urls <- c(
  "https://www.guttmacher.org/state-policy/explore/state-policies-abortion-bans", 
  "https://www.guttmacher.org/state-policy/explore/targeted-regulation-abortion-providers", 
  "https://www.guttmacher.org/state-policy/explore/counseling-and-waiting-periods-abortion",
  "https://www.guttmacher.org/state-policy/explore/medication-abortion", 
  "https://www.guttmacher.org/state-policy/explore/abortion-reporting-requirements"
)

# Loop through URLs and scrape each
tables <- lapply(urls, scrape_tables)

