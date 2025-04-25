# Load required packages
library(httr)
library(jsonlite)
library(dplyr)
library(lubridate)
library(purrr)

nyt_key <- "FdWOerQ3A95zaAvdCuRX1aRu4H0TyoiO"

# Safe fetch function with logging
get_articles_by_month <- function(year, month) {
  url <- paste0("https://api.nytimes.com/svc/archive/v1/", year, "/", month, ".json?api-key=", nyt_key)
  res <- GET(url)
  
  if (status_code(res) != 200) {
    message("Request failed for ", year, "-", sprintf("%02d", month),
            " (Status code: ", status_code(res), ")")
    return(NULL)
  }
  
  content_raw <- content(res, as = "text", encoding = "UTF-8")
  docs <- fromJSON(content_raw)$response$docs
  
  docs |>
    filter(grepl("abortion", paste(headline$main, snippet, lead_paragraph), ignore.case = TRUE)) |>
    transmute(
      title = headline$main,
      description = snippet,
      url = web_url,
      publicationDate = substr(pub_date, 1, 10),
      category = section_name,
      authors = byline$original,
      text = ifelse(!is.na(lead_paragraph), lead_paragraph, snippet)
    )
}

# Create date grid
date_grid <- expand.grid(year = 2020:2024, month = 1:12) |> arrange(year, month)

# Optional: Load previous progress (if resuming)
save_path <- "nyt_abortion_articles_cleaned.csv"
existing <- if (file.exists(save_path)) read.csv(save_path) else NULL

# Run the loop with 12-second delay
nyt_abortion_articles <- map2_dfr(date_grid$year, date_grid$month, function(year, month) {
  Sys.sleep(12)  # Respect 5 calls per minute limit
  get_articles_by_month(year, month)
})

# Append to existing file if resuming
final_articles <- if (!is.null(existing)) bind_rows(existing, nyt_abortion_articles) else nyt_abortion_articles

# Save result
write_csv(final_articles, "../data/nyt_news_data.csv")

