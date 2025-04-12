# ==============================================================================
# Purpose: Scrape and extract data from 
# Main Author: Erika Salvador
# Contributors: Maigan Lafontant, Emilie Ward
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

# ==============================================================================
# Check scraping permissions
# ==============================================================================
paths_allowed("https://www.foxnews.com/?msockid=1f6d91ae1b076b4d3b0d85791a9a6afe")

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
# Dynamic Scraper for Fox News Abortion Archive with Load More Handling
# ==============================================================================
get_fox_article_links_dynamic <- function(scroll_limit = 20) {
  port <- find_free_port()
  rD <- rsDriver(
    browser = "chrome",
    chromever = NULL,
    chromeverpath = "drivers/chromedriver-win64/chromedriver.exe",
    port = port,
    verbose = FALSE
  )
  
  remDr <- rD$client
  remDr$navigate("https://www.foxnews.com/category/politics/judiciary/abortion")
  Sys.sleep(3)
  
  for (i in 1:scroll_limit) {
    cat("Show more click:", i, "\n")
    tryCatch({
      load_more <- remDr$findElement(using = "css selector", value = ".button.load-more.js-load-more")
      load_more$clickElement()
      Sys.sleep(2.5)
    }, error = function(e) {
      cat("No more 'Show More' button after", i, "clicks.\n")
      break
    })
  }
  
  html_source <- remDr$getPageSource()[[1]]
  page <- read_html(html_source)
  
  remDr$close()
  rD$server$stop()
  
  links <- page |>
    html_elements("div.content.article-list article.article h4.title a") |>
    html_attr("href") |>
    unique()
  
  links <- ifelse(startsWith(links, "/"), paste0("https://www.foxnews.com", links), links)
  return(unique(links))
}

# ==============================================================================
# Scrape full article content and filter by abortion mentions
# ==============================================================================
scrape_article_direct <- function(url) {
  Sys.sleep(runif(1, 1.5, 2.5))
  page <- tryCatch(read_html(url), error = function(e) return(NULL))
  if (is.null(page)) return(NULL)
  
  text <- page |>
    html_elements("p") |>
    html_text2() |>
    paste(collapse = " ")
  
  count <- str_count(str_to_lower(text), "\\babortion\\b")
  if (count < 5) return(NULL)
  
  title <- page |>
    html_element("h1.headline.speakable") |>
    html_text2()
  
  author <- page |>
    html_elements("meta[name='author'], .byline, .author, div.author-byline") |>
    html_attr("content") |>
    na.omit()
  
  if (length(author) == 0) {
    p_text <- page |>
      html_elements("p") |>
      html_text2()
    
    author <- p_text[str_detect(p_text, "^By \\w")] |>
      str_remove("^By ")
  }
  
  author <- paste(unique(author), collapse = "; ")
  
  date <- page |>
    html_elements("meta[property='article:published_time'], time") |>
    html_attr("content") |>
    str_extract("\\d{4}-\\d{2}-\\d{2}") |>
    na.omit()
  
  date <- date[1]
  
  return(tibble(
    url = url,
    title = title,
    author = author,
    article_date = date,
    abortion_mentions = count,
    full_text = text
  ))
}

# ==============================================================================
# Run Fox News Dynamic Pipeline and Save Output with Safety and Logging
# ==============================================================================
cat("Starting Fox News abortion archive scrape...\n")

fox_links <- get_fox_article_links_dynamic(scroll_limit = 20)

cat("Collected", length(fox_links), "article URLs\n")

# Initialize progress bar
pb <- progress_bar$new(
  format = "  Scraping [:bar] :percent in :elapsed",
  total = length(fox_links), clear = FALSE, width = 60
)

# Prep result storage
abortion_articles_fox <- tibble()
failed_urls <- c()
skipped_date_count <- 0

# Loop with safety + retries + filtering
for (i in seq_along(fox_links)) {
  pb$tick()
  url <- fox_links[i]
  
  # Try scraping with retry logic
  article <- tryCatch(scrape_article_direct(url), error = function(e) NULL)
  if (is.null(article)) {
    Sys.sleep(2)
    article <- tryCatch(scrape_article_direct(url), error = function(e) NULL)
  }
  
  # Skip and log if still NULL
  if (is.null(article)) {
    failed_urls <- c(failed_urls, url)
    write_lines(url, "failed_urls.txt", append = TRUE)
    next
  }
  
  # Date filter: keep only 2009–2024 articles
  parsed_date <- suppressWarnings(ymd(article$article_date))
  if (is.na(parsed_date) || parsed_date < ymd("2009-01-01") || parsed_date > ymd("2024-12-31")) {
    skipped_date_count <- skipped_date_count + 1
    next
  }
  
  # Append to master tibble
  abortion_articles_fox <- bind_rows(abortion_articles_fox, article)
  
  # Save partial results every 10 valid articles
  if (nrow(abortion_articles_fox) %% 10 == 0) {
    write.csv(abortion_articles_fox, "abortion_articles_fox_temp.csv", row.names = FALSE)
  }
}

# Final save
write.csv(abortion_articles_fox, "abortion_articles_fox.csv", row.names = FALSE)

cat("\nDone! ", nrow(abortion_articles_fox), " articles saved to abortion_articles_fox.csv\n")
cat("\nUnfortunately,", length(failed_urls), " failed URLs logged to failed_urls.txt\n")
cat("\nSkipped", skipped_date_count, "articles due to out-of-range publish dates.\n")