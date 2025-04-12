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


