import os
import re
import json
import statistics
import sqlite3
from datetime import datetime
import time

import requests
from pdfminer.high_level import extract_text

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

BASE_URL = "https://jnportal.ujn.gov.rs"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*"
}

os.makedirs("documents", exist_ok=True)

conn = sqlite3.connect("contracts.db")
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS tenders (
    entity_id INTEGER PRIMARY KEY,
    lowest REAL,
    median REAL,
    accepted REAL,
    risk INTEGER,
    created_at TEXT
)
""")
conn.commit()


def exists(eid):
    c.execute("SELECT 1 FROM tenders WHERE entity_id=?", (eid,))
    return c.fetchone() is not None


# =========================
# FETCH IDS (SELENIUM)
# =========================
def fetch_entity_ids():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=options)

    try:
        driver.get("https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora")
        time.sleep(6)

        links = driver.find_elements("xpath", "//a[contains(@href, '/tender-eo/')]")

        ids = []

        for l in links:
            href = l.get_attribute("href")

            if not href:
                continue

            match = re.search(r'/tender-eo/(\d+)', href)

            if match:
                eid = int(match.group(1))
                if not exists(eid):
                    ids.append(eid)

        ids = list(dict.fromkeys(ids))

        print("NEW IDS:", ids[:10])
        return ids[:10]   # samo 10 za debug

    finally:
        driver.quit()


# =========================
# DOWNLOAD PDF (DEBUG)
# =========================
def download_pdf(tender_id):
    try:
        print("\n====================")
        print("OPEN:", tender_id)

        url = f"{BASE_URL}/tender-eo/{tender_id}"

        r = requests.get(url, headers=HEADERS, timeout=30)

        print("URL:", url)

        if r.status_code != 200:
            print("PAGE FAIL:", r.status_code)
            return None

        html = r.text

        match = re.search(r'"entityId"\s*:\s*(\d+)', html)

        print("ENTITY MATCH:", match)
        print("HTML SAMPLE:", html[:500])

        return None  # za sada samo debug

    except Exception as e:
        print("ERROR:", e)
        return None


# =========================
# MAIN
# =========================
def main():
    ids = fetch_entity_ids()

    for eid in ids:
        download_pdf(eid)

    print("DONE")


if __name__ == "__main__":
    main()
