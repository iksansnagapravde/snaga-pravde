import json
import sqlite3
import time
from datetime import datetime

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

BASE_URL = "https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora"
DB_FILE = "contracts.db"

START_DATE = datetime(2026, 1, 1)


# =====================================================
# DRIVER
# =====================================================

def create_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=chrome_options)


# =====================================================
# DB
# =====================================================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS contracts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT UNIQUE,
        supplier TEXT,
        date TEXT,
        amount REAL
    )
    """)

    conn.commit()
    conn.close()


# =====================================================
# GET LATEST DECISIONS
# =====================================================

def get_latest_decisions(driver):
    driver.get(BASE_URL)
    time.sleep(3)

    soup = BeautifulSoup(driver.page_source, "html.parser")

    rows = soup.find_all("tr")[:20]

    decisions = []

    for row in rows:
        text = row.get_text(" ", strip=True)
        if text:
            decisions.append(text)

    return decisions


# =====================================================
# MAIN
# =====================================================

def main():
    init_db()

    driver = create_driver()

    decisions = get_latest_decisions(driver)

    print("=" * 40)
    print("LATEST DECISIONS FOUND:")
    print("=" * 40)

    for d in decisions:
        print(d)

    driver.quit()


if __name__ == "__main__":
    main()
