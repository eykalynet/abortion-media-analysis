# ==============================================================================
# Purpose: Scrape and extract data from 
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

