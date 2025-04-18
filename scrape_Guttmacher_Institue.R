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

# ==============================================================================
# Check scraping permissions
# ==============================================================================
paths_allowed("https://www.guttmacher.org/state-legislation-tracker")

#returned true for scraping


 <- rhyme_url |>               
  read_html() |>
  #find .poem by inspecting the code
  html_element(".poem") |> 
  html_text2()

#rhyme
cat(rhyme)


