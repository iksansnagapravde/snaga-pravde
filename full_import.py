import os
import re
import statistics
import sqlite3
from datetime import datetime
import time

import requests
from pdfminer.high_level import extract_text

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

BASE_URL = "https://jnportal.ujn.gov.rs"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*"
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
# FETCH IDS
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

        links = driver.find_elements(By.XPATH, "//a[contains(@href, '/tender-eo/')]")

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
        return ids[:10]

    finally:
        driver.quit()


# =========================
# DOWNLOAD FILE (PRAVI FIX)
# =========================
def download_file(tender_id):
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

        # 🔥 klik na expand strelicu (PRAVA META)
        expand_buttons = driver.find_elements(
            By.XPATH,
            "//mat-icon[contains(text(),'expand_more') or contains(text(),'keyboard_arrow_down')]"
        )

        clicked = False

        for btn in expand_buttons:
            try:
                driver.execute_script("arguments[0].click();", btn)
                clicked = True
                break
            except:
                continue

        if not clicked:
            print("NO EXPAND:", tender_id)
            return None

        # 🔥 čekaj da se pojave dokumenti
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//a[contains(@href,'.pdf') or contains(@href,'.doc')]")
                )
            )
        except:
            print("NO DOC LOADED:", tender_id)
            return None

        # 🔥 uzmi samo dokument linkove
        links = driver.find_elements(
            By.XPATH,
            "//a[contains(@href,'.pdf') or contains(@href,'.doc')]"
        )

        for l in links:
            href = l.get_attribute("href")

            if not href:
                continue

            print("DOC:", href)

            r = requests.get(href, headers=HEADERS)

            if r.status_code != 200:
                continue

            path = f"documents/{tender_id}"

            if ".pdf" in href:
                path += ".pdf"
            else:
                path += ".docx"

            with open(path, "wb") as f:
                f.write(r.content)

            print("FILE SAVED:", path)
            return path

        print("NO DOCUMENT:", tender_id)
        return None

    finally:
        driver.quit()


# =========================
# TEXT
# =========================
def extract_text_safe(path):
    try:
        if path.endswith(".pdf"):
            text = extract_text(path)
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
        file_path = download_file(eid)

        if not file_path:
            continue

        text = extract_text_safe(file_path)

        if len(text) < 100:
            continue

        prices = extract_bids(text)

        result = analyze(prices)

        if result:
            save(eid, result)

    print("DONE")


if __name__ == "__main__":
    main()
