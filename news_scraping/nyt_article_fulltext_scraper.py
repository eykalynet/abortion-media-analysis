# ==============================================================================
# Purpose: Scrape full-text content for NYT abortion articles using nyt-scraper
# Author: Erika Salvador
# Date: 2025-04-25
# ==============================================================================

# ==============================================================================
# INSTALL REQUIRED PACKAGE (run in terminal once)
# pip install nyt-scraper
# ==============================================================================

import pandas as pd
from nyt_scraper.article import get_article
from tqdm import tqdm

# ==============================================================================
# LOAD METADATA
# ------------------------------------------------------------------------------
# Load the existing metadata CSV from your R Archive API step.
# ==============================================================================
nyt_df = pd.read_csv("../data/nyt_news_data.csv")

# ==============================================================================
# GET FULL ARTICLE TEXT USING nyt-scraper
# ------------------------------------------------------------------------------
# This function will fetch and parse the full text from each article.
# It uses NYT's public page structure and returns cleaned main body text.
# ==============================================================================

def fetch_full_article(url):
    try:
        article = get_article(url)
        return article["text"]
    except Exception as e:
        print(f"Error fetching: {url}\n{e}")
        return None

# Add progress bar
tqdm.pandas()
nyt_df["full_text"] = nyt_df["url"].progress_apply(fetch_full_article)

# ==============================================================================
# SAVE TO CSV
# ------------------------------------------------------------------------------
# Save the full dataset with full_text appended.
# ==============================================================================
nyt_df.to_csv("../data/nyt_news_data_fulltext_nytscraper.csv", index=False)
print("✅ Done! Saved to nyt_news_data_fulltext_nytscraper.csv")
