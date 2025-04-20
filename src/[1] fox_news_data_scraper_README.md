# Abortion News Article Scraper

## Scrape and extract abortion news data from Fox News (2020-2024)

This project collects news articles related to abortion from the Fox News website, specifically within its Judiciary section. The scraper targets articles published between January 2020 and December 2024 and extracts their titles, full text, publication dates, and URLs. The output is stored in a structured JSON format.

The scraper operates by simulating a web browser that can interact with modern JavaScript-heavy pages. It scrolls through content, opens articles, and extracts information while minimizing the chances of being blocked. It uses tools such as headless browsing and IP rotation via Tor.

## Requirements

To run this project, you need the following:

-   Python 3.8 or newer

-   A working installation of Tor (used by the stem library)

The following Python packages:

-   selenium

-   selenium-driverless

-   nltk

-   stem

-   lxml

-   curl-cffi

-   cdp-socket

## Reproducibility

This project is built in Python and involves tools that go slightly beyond the standard RStudio and GitHub workflow used in many settings. Unlike R projects where a repository can simply be cloned and opened in an `.Rproj` file, this setup requires working in a Python environment with external libraries. It takes a few extra steps, but once things are set up, the workflow is (hopefully) just as smooth.

**Step 1: Clone the repository.**\
The repository can be cloned using either GitHub Desktop or the SSH method introduced earlier in STAT231. For most setups, one can simply open **GitHub Desktop**, click on **“File” → “Clone Repository”**, and paste the repository URL:

```         
https://github.com/acstat231-s25/blog-MEE.git
```

Then select a local path and clone. Alternatively, for those who have configured SSH keys for GitHub (as practiced earlier in the course), the repository can be cloned using the terminal:

```         
git clone git@github.com:acstat231-s25/blog-MEE.git
cd blog-MEE
```

This project follows the same GitHub setup covered at the beginning of STAT231, but instead of working with `.Rproj` files in RStudio, it includes Python scripts and terminal-based tools that require a few extra setup steps.
