import os
import re
import json
import time
import statistics
import sqlite3
from datetime import datetime

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

from pdf2image import convert_from_path
import pytesseract

BASE_URL = "https://jnportal.ujn.gov.rs"

# ✅ STABILAN TOKEN (ključ za 401 problem)
USER_TOKEN = "746fd3d9-a658-4559-aff6-ce28c6621268"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora",
    "Origin": "https://jnportal.ujn.gov.rs",
    "Connection": "keep-alive",
    "X-Requested-With": "XMLHttpRequest"
}

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

# =========================================
# FETCH IDS
# =========================================
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

        links = driver.find_elements(By.XPATH, "//a[contains(@href, '/tender-eo/')]")

        for link in links:
            href = link.get_attribute("href")
            if href:
                try:
                    ids.append(int(href.split("/")[-1]))
                except:
                    continue

        ids = list(set(ids))
        print("FOUND IDS:", ids)
        return ids

    finally:
        driver.quit()

# =========================================
# DOWNLOAD PDF (FIX 401)
# =========================================
def download_pdf(entity_id):
    try:
        url = f"https://jnportal.ujn.gov.rs/api/searchgrid/VAwardDecisions/get?skip=0&take=10"

        r = requests.get(url, headers=HEADERS, timeout=30)

        if r.status_code != 200:
            print("API FAIL:", r.status_code)
            return None

        data = r.json()

        # 🔍 nađi naš entity
        for item in data:
            if item.get("Id") == entity_id:
                doc_id = item.get("DocumentId") or item.get("Id")

                if not doc_id:
                    continue

                pdf_url = f"https://jnportal.ujn.gov.rs/GetDocuments.ashx?entityId={doc_id}&objectMetaId=2&documentGroupId=169&associationTypeId=1"

                print("PDF URL:", pdf_url)

                pdf = requests.get(pdf_url, headers=HEADERS, timeout=60)

                if pdf.status_code != 200:
                    continue

                if not pdf.content.startswith(b"%PDF"):
                    print("NOT PDF")
                    continue

                path = f"documents/{entity_id}.pdf"

                with open(path, "wb") as f:
                    f.write(pdf.content)

                print("PDF SAVED")
                return path

        print("NOT FOUND:", entity_id)
        return None

    except Exception as e:
        print("DOWNLOAD ERROR:", e)
        return None

# =========================================
# OCR
# =========================================
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

# =========================================
# EXTRACT PRICES (FIX)
# =========================================
def extract_prices(text):
    prices = []

    pattern = r"\d{1,3}(?:\.\d{3})*,\d{2}"
    matches = re.findall(pattern, text)

    for m in matches:
        try:
            num = float(m.replace(".", "").replace(",", "."))

            if num < 500000:
                continue

            if num > 10_000_000_000:
                continue

            prices.append(num)

        except ValueError:
            continue

    prices = sorted(set(prices))

    if prices:
        max_price = max(prices)
        prices = [p for p in prices if p > max_price * 0.2]

    print("PRICES:", prices)

    return prices

# =========================================
# ACCEPTED
# =========================================
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

# =========================================
# ANALYZE
# =========================================
def analyze(prices, accepted):
    if len(prices) < 2:
        return None

    lowest = min(prices)
    medijana = statistics.median(prices)

    if not accepted:
        accepted = prices[-1]

    loss_low = max(0, accepted - lowest)
    loss_med = max(0, accepted - medijana)

    return lowest, medijana, accepted, loss_low, loss_med

# =========================================
# SAVE
# =========================================
def save(eid, data):
    c.execute("""
    INSERT OR IGNORE INTO tenders
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (eid, *data, datetime.now().isoformat()))
    conn.commit()

def exists(eid):
    c.execute("SELECT 1 FROM tenders WHERE entity_id=?", (eid,))
    return c.fetchone() is not None

# =========================================
# STATS (FIX)
# =========================================
def write_stats():
    c.execute("SELECT lowest, accepted FROM tenders")
    rows = c.fetchall()

    if not rows:
        stats = {
            "broj_tendera": 0,
            "ukupna_vrednost": "0 RSD",
            "ukupna_vrednost_eur": "0 EUR",
            "broj_ugovora": 0,
            "ugovorena_vrednost": "0 RSD",
            "ugovorena_vrednost_eur": "0 EUR"
        }
    else:
        total_lowest = sum(r[0] for r in rows)
        total_accepted = sum(r[1] for r in rows)
        kurs = 117.2

        stats = {
            "broj_tendera": len(rows),
            "ukupna_vrednost": f"{round(total_lowest, 2)} RSD",
            "ukupna_vrednost_eur": f"{round(total_lowest / kurs, 2)} EUR",
            "broj_ugovora": len(rows),
            "ugovorena_vrednost": f"{round(total_accepted, 2)} RSD",
            "ugovorena_vrednost_eur": f"{round(total_accepted / kurs, 2)} EUR"
        }

    with open("stats.json", "w") as f:
        json.dump(stats, f, indent=2)

# =========================================
# LOSS DATA
# =========================================
def write_loss_data():
    c.execute("SELECT lowest, medijana, accepted, loss_low, loss_medijana FROM tenders")
    rows = c.fetchall()

    if not rows:
        data = {
            "najbolja_ponuda": 0,
            "medijana_ponuda": 0,
            "prihvacena_ponuda": 0,
            "broj_analiziranih": 0,
            "gubitak_prema_najboljoj": 0,
            "gubitak_prema_medijani": 0,
            "valuta_kurs_eur": 117.2
        }
    else:
        data = {
            "najbolja_ponuda": round(sum(r[0] for r in rows), 2),
            "medijana_ponuda": round(sum(r[1] for r in rows), 2),
            "prihvacena_ponuda": round(sum(r[2] for r in rows), 2),
            "broj_analiziranih": len(rows),
            "gubitak_prema_najboljoj": round(sum(r[3] for r in rows), 2),
            "gubitak_prema_medijani": round(sum(r[4] for r in rows), 2),
            "valuta_kurs_eur": 117.2
        }

    with open("loss-data.json", "w") as f:
        json.dump(data, f, indent=2)

# =========================================
# MAIN
# =========================================
def main():
    ids = fetch_entity_ids()

    for eid in ids:
        if exists(eid):
            continue

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

    write_stats()
    write_loss_data()

    print("DONE")

if __name__ == "__main__":
    main()
