import os
import re
import json
import statistics
import sqlite3
import time
from datetime import datetime

import requests
from pdf2image import convert_from_path
import pytesseract

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

BASE_URL = "https://jnportal.ujn.gov.rs"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*"
}

# =========================
# SETUP
# =========================
os.makedirs("documents", exist_ok=True)

conn = sqlite3.connect("contracts.db")
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS tenders (
    entity_id INTEGER PRIMARY KEY,
    lowest REAL,
    medijana REAL,
    accepted REAL,
    loss_low REAL,
    loss_medijana REAL,
    created_at TEXT
)
""")

conn.commit()

# =========================
# EXISTS
# =========================
def exists(eid):
    c.execute("SELECT 1 FROM tenders WHERE entity_id=?", (eid,))
    return c.fetchone() is not None

# =========================
# FETCH
# =========================
def fetch_entity_ids():
    ids = []

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=options)

    try:
        driver.get("https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora")
        time.sleep(5)

        html = driver.page_source

        found = re.findall(r"/tender-eo/(\d+)", html)
        found = list(dict.fromkeys(found))

        print("FOUND RAW:", found[:10])

        found = found[:10]

        for eid in found:
            eid = int(eid)
            if not exists(eid):
                ids.append(eid)

        print("LAST 10 IDS:", ids)
        return ids

    finally:
        driver.quit()

# =========================
# DOWNLOAD PDF
# =========================
def download_pdf(_):
    from selenium.webdriver.common.by import By

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    prefs = {
        "download.default_directory": os.path.abspath("documents"),
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True
    }
    options.add_experimental_option("prefs", prefs)

    driver = webdriver.Chrome(options=options)

    try:
        driver.get("https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora")
        time.sleep(5)

        rows = driver.find_elements(By.XPATH, "//div[contains(@class,'mat-row')]")

        if not rows:
            print("NO ROWS")
            return None

        row = rows[0]

        try:
            btn = row.find_element(By.XPATH, ".//button")
            driver.execute_script("arguments[0].click();", btn)

            print("CLICKED DOWNLOAD")

            time.sleep(5)

        except:
            print("NO BUTTON")
            return None

        files = os.listdir("documents")
        pdfs = [f for f in files if f.endswith(".pdf")]

        if pdfs:
            latest = max(pdfs, key=lambda x: os.path.getmtime(os.path.join("documents", x)))
            print("DOWNLOADED:", latest)
            return os.path.join("documents", latest)

        print("NO PDF FOUND")
        return None

    finally:
        driver.quit()

# =========================
# OCR
# =========================
def extract_text(pdf_path):
    text = ""

    try:
        images = convert_from_path(pdf_path, dpi=300)
        print("IMAGES:", len(images))

        for img in images:
            t = pytesseract.image_to_string(img, config="--psm 6")
            text += t + "\n"

    except Exception as e:
        print("OCR ERROR:", e)

    return text

# =========================
# PRICES
# =========================
def extract_prices(text):
    prices = []

    matches = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", text)

    for m in matches:
        try:
            num = float(m.replace(".", "").replace(",", "."))

            if num < 500000:
                continue

            if num > 1_000_000_000:
                continue

            prices.append(num)

        except:
            continue

    prices = sorted(set(prices))

    if prices:
        max_price = max(prices)
        prices = [p for p in prices if p > max_price * 0.2]

    print("FINAL PRICES:", prices)

    return prices

# =========================
# ACCEPTED
# =========================
def find_accepted(text):
    lines = text.split("\n")

    for i, line in enumerate(lines):
        if "изабрана" in line.lower() or "најповољнија" in line.lower():
            for j in range(i, i + 5):
                if j < len(lines):
                    m = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", lines[j])
                    if m:
                        try:
                            return float(m[0].replace(".", "").replace(",", "."))
                        except:
                            continue

    return None

# =========================
# ANALYZE
# =========================
def analyze(prices, accepted):
    if len(prices) < 1:
        return None

    lowest = min(prices)
    medijana = statistics.median(prices) if len(prices) > 1 else lowest

    if not accepted:
        accepted = prices[-1]

    loss_low = max(0, accepted - lowest)
    loss_med = max(0, accepted - medijana)

    return lowest, medijana, accepted, loss_low, loss_med

# =========================
# SAVE
# =========================
def save(eid, data):
    c.execute("""
    INSERT OR IGNORE INTO tenders VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (eid, *data, datetime.now().isoformat()))
    conn.commit()

# =========================
# MAIN
# =========================
def main():
    ids = fetch_entity_ids()

    for eid in ids:
        print("PROCESS:", eid)

        pdf = download_pdf(eid)
        if not pdf:
            continue

        text = extract_text(pdf)

        if len(text) < 100:
            continue

        prices = extract_prices(text)
        accepted = find_accepted(text)

        result = analyze(prices, accepted)

        if result:
            save(eid, result)

    print("DONE")


if __name__ == "__main__":
    main()
