import os
import re
import json
import statistics
import sqlite3
from datetime import datetime

import requests
from pdf2image import convert_from_path
import pytesseract

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
# FETCH NEW IDS (SMART)
# =========================
def fetch_entity_ids():
    ids = []

    try:
        for page in range(0, 50):
            skip = page * 10

            url = f"{BASE_URL}/api/searchgrid/VAwardDecisions/get?skip={skip}&take=10"

            r = requests.get(url, headers=HEADERS, timeout=30)

            if r.status_code != 200:
                print("BAD STATUS:", r.status_code)
                continue

            data = r.json()

            if not data:
                break

            stop = False

            for item in data:
                eid = item.get("Id")

                if not eid:
                    continue

                if exists(eid):
                    print("STOP — already in DB:", eid)
                    stop = True
                    break

                ids.append(eid)

            if stop:
                break

        print("NEW IDS:", ids)
        return ids

    except Exception as e:
        print("FETCH ERROR:", e)
        return []

# =========================
# DOWNLOAD PDF (DIRECT)
# =========================
def download_pdf(entity_id):
    try:
        pdf_url = f"{BASE_URL}/GetDocuments.ashx?entityId={entity_id}&objectMetaId=2&documentGroupId=169&associationTypeId=1"

        print("TRY:", pdf_url)

        r = requests.get(pdf_url, headers=HEADERS, timeout=60)

        if r.status_code != 200:
            print("FAIL:", r.status_code)
            return None

        if not r.content.startswith(b"%PDF"):
            print("NOT PDF")
            return None

        path = f"documents/{entity_id}.pdf"

        with open(path, "wb") as f:
            f.write(r.content)

        print("PDF SAVED:", entity_id)
        return path

    except Exception as e:
        print("DOWNLOAD ERROR:", e)
        return None

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
# EXTRACT PRICES
# =========================
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

# =========================
# ACCEPTED DETECTION
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
    if len(prices) < 2:
        return None

    lowest = min(prices)
    medijana = statistics.median(prices)

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
    INSERT OR IGNORE INTO tenders
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (eid, *data, datetime.now().isoformat()))
    conn.commit()

# =========================
# STATS
# =========================
def write_stats():
    c.execute("SELECT lowest, accepted FROM tenders")
    rows = c.fetchall()

    kurs = 117.2

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

# =========================
# LOSS DATA
# =========================
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

    write_stats()
    write_loss_data()

    print("DONE")

if __name__ == "__main__":
    main()
