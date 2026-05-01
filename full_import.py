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
        return ids

    finally:
        driver.quit()


# =========================
# DOWNLOAD PDF (SELENIUM SESSION)
# =========================
def download_pdf(tender_id):
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=options)

    try:
        print("PROCESS:", tender_id)

        url = f"{BASE_URL}/tender-eo/{tender_id}"
        driver.get(url)
        time.sleep(5)

        html = driver.page_source

        match = re.search(r'"entityId"\s*:\s*(\d+)', html)

        if not match:
            print("NO ENTITY:", tender_id)
            return None

        entity_id = match.group(1)
        print("ENTITY:", entity_id)

        api_url = f"{BASE_URL}/get-documents?entityId={entity_id}&objectMetaId=2&documentGroupId=169&associationTypeId=1"

        r = requests.get(api_url, headers=HEADERS)

        if r.status_code != 200:
            print("DOC FAIL:", tender_id)
            return None

        data = r.json()

        for doc in data:
            url = doc.get("DocumentUrl")

            if not url:
                continue

            full = BASE_URL + url

            pdf = requests.get(full, headers=HEADERS)

            if pdf.status_code != 200:
                continue

            if not pdf.content.startswith(b"%PDF"):
                continue

            path = f"documents/{tender_id}.pdf"

            with open(path, "wb") as f:
                f.write(pdf.content)

            print("PDF SAVED:", tender_id)
            return path

        print("NO PDF:", tender_id)
        return None

    finally:
        driver.quit()


# =========================
# TEXT
# =========================
def extract_text_safe(pdf_path):
    try:
        text = extract_text(pdf_path)
        if text and len(text) > 200:
            return text
    except:
        pass
    return ""


# =========================
# BIDS
# =========================
def extract_bids(text):
    prices = []

    matches = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", text)

    for m in matches:
        try:
            num = float(m.replace(".", "").replace(",", "."))

            if num < 10000 or num > 1_000_000_000:
                continue

            prices.append(num)

        except:
            continue

    prices = sorted(set(prices))

    print("PRICES:", prices)
    return prices


# =========================
# ANALYZE
# =========================
def analyze(prices):
    if len(prices) < 1:
        return None

    lowest = min(prices)
    median = statistics.median(prices) if len(prices) > 1 else lowest
    accepted = lowest

    risk = 0

    if accepted > lowest:
        risk += 3

    if accepted > median * 1.2:
        risk += 2

    if len(prices) <= 2:
        risk += 2

    return lowest, median, accepted, risk


# =========================
# SAVE
# =========================
def save(eid, data):
    c.execute("""
    INSERT OR REPLACE INTO tenders
    VALUES (?, ?, ?, ?, ?, ?)
    """, (eid, *data, datetime.now().isoformat()))
    conn.commit()


# =========================
# MAIN
# =========================
def main():
    ids = fetch_entity_ids()

    for eid in ids:
        pdf = download_pdf(eid)

        if not pdf:
            continue

        text = extract_text_safe(pdf)

        if len(text) < 100:
            continue

        prices = extract_bids(text)

        result = analyze(prices)

        if result:
            save(eid, result)

    print("DONE")


if __name__ == "__main__":
    main()
