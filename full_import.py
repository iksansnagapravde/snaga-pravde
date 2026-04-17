import os
import re
import json
import time
import statistics
import sqlite3
from datetime import datetime

import pdfplumber
import requests

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

BASE_URL = "https://jnportal.ujn.gov.rs"

# =========================================
# SESSION (za PDF)
# =========================================
session = requests.Session()

# =========================================
# FOLDER
# =========================================
os.makedirs("documents", exist_ok=True)

# =========================================
# DATABASE
# =========================================
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
# AUTO FIX DATABASE
# =========================================
def fix_database():
    try:
        c.execute("PRAGMA table_info(tenders)")
        columns = [col[1] for col in c.fetchall()]

        if "medijana" not in columns:
            print("RESET DATABASE")

            c.execute("DROP TABLE IF EXISTS tenders")

            c.execute("""
            CREATE TABLE tenders (
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
    except Exception as e:
        print("DB FIX ERROR:", e)

fix_database()

# =========================================
# SELENIUM FETCH IDS
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
                    eid = int(href.split("/")[-1])
                    ids.append(eid)
                except:
                    pass

        ids = list(set(ids))
        print("FOUND IDS:", ids)

        return ids

    finally:
        driver.quit()

# =========================================
# DOWNLOAD PDF
# =========================================
def download_pdf(entity_id):
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")

    driver = webdriver.Chrome(options=options)

    try:
        # 🔥 OTVORI STRANICU (da dobije cookies)
        driver.get("https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora")
        time.sleep(3)

        # 🔥 UZMI COOKIES
        cookies = driver.get_cookies()

        session = requests.Session()
        for cookie in cookies:
            session.cookies.set(cookie['name'], cookie['value'])

        # 🔥 API POZIV (SADA RADI)
        api_url = f"https://jnportal.ujn.gov.rs/get-documents?entityId={entity_id}&objectMetaId=2&documentGroupId=169&associationTypeId=1&prefetch=true"

        r = session.get(api_url, headers=HEADERS, timeout=30)

        if r.status_code != 200:
            print("DOC API FAIL:", r.status_code)
            return None

        data = r.json()

        if not data:
            print("NO DOCUMENTS:", entity_id)
            return None

        # 🔥 NAĐI PDF
        for doc in data:
            name = doc.get("FileName", "").lower()
            url = doc.get("DocumentUrl")

            if not url:
                continue

            if name.endswith(".pdf"):
                full_url = "https://jnportal.ujn.gov.rs" + url

                print("PDF:", name)

                pdf = session.get(full_url, headers=HEADERS, timeout=60)

                if pdf.status_code != 200:
                    continue

                if not pdf.content.startswith(b"%PDF"):
                    print("NOT REAL PDF")
                    continue

                path = f"documents/{entity_id}.pdf"

                with open(path, "wb") as f:
                    f.write(pdf.content)

                print("PDF SAVED")
                return path

        print("NO VALID PDF:", entity_id)
        return None

    except Exception as e:
        print("DOWNLOAD ERROR:", e)

    finally:
        driver.quit()

    return None
# =========================================
# EXTRACT TEXT
# =========================================
from pdf2image import convert_from_path
import pytesseract

def extract_text(pdf_path):
    text = ""

    try:
        images = convert_from_path(
            pdf_path,
            dpi=300,
            poppler_path="/usr/bin"
        )

        print("IMAGES:", len(images))

        for img in images:
            t = pytesseract.image_to_string(img)
            text += t + "\n"

    except Exception as e:
        print("OCR ERROR:", e)

    return text
# =========================================
# EXTRACT PRICES
# =========================================
def extract_prices(text):
    prices = []

    # hvata sve brojeve tipa: 1.234.567,89
    matches = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", text)

    for m in matches:
        try:
            num = float(m.replace(".", "").replace(",", "."))
            prices.append(num)
        except:
            pass

    return prices

# =========================================
# FIND ACCEPTED
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
                            pass

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

    return lowest, medijana, accepted, accepted - lowest, accepted - medijana

# =========================================
# EXISTS
# =========================================
def exists(eid):
    c.execute("SELECT 1 FROM tenders WHERE entity_id=?", (eid,))
    return c.fetchone() is not None

# =========================================
# SAVE
# =========================================
def save(eid, data):
    c.execute("""
    INSERT OR IGNORE INTO tenders
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (eid, *data, datetime.now().isoformat()))
    conn.commit()

# =========================================
# STATS
# =========================================
def write_stats():
    c.execute("SELECT COUNT(*) FROM tenders")
    count = c.fetchone()[0]

    stats = {
        "broj_tendera": count,
        "ukupna_vrednost": "0 RSD",
        "ukupna_vrednost_eur": "0 EUR",
        "broj_ugovora": count,
        "ugovorena_vrednost": "0 RSD",
        "ugovorena_vrednost_eur": "0 EUR"
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

    print("IDS:", ids)

    for eid in ids:
        if exists(eid):
            continue

        print("PROCESS:", eid)

        pdf = download_pdf(eid)
        if not pdf:
            continue

        text = extract_text(pdf)

        # 🔥 DEBUG (KLJUČNO)
        print("TEXT LENGTH:", len(text))
        print("SAMPLE TEXT:", text[:500])
        print("----------")

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
