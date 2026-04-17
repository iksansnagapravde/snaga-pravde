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
# FETCH IDS (HTML, NO API, NO 401)
# =========================
def fetch_entity_ids():
    ids = []

    try:
        url = "https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora"

        r = requests.get(url, headers=HEADERS, timeout=30)

        if r.status_code != 200:
            print("BAD STATUS:", r.status_code)
            return []

        html = r.text

        found = re.findall(r"/tender-eo/(\d+)", html)

        print("FOUND RAW:", found[:10])  # 👈 DEBUG

        found = found[:10]

        for eid in found:
            eid = int(eid)

            if not exists(eid):
                ids.append(eid)

        print("LAST 10 IDS:", ids)
        return ids

    except Exception as e:
        print("FETCH ERROR:", e)
        return []
# =========================
# DOWNLOAD PDF (FIX ENTITY ID)
# =========================
def download_pdf(tender_id):
    try:
        # 1. otvori tender stranicu
        url = f"{BASE_URL}/tender-eo/{tender_id}"

        r = requests.get(url, headers=HEADERS, timeout=30)

        if r.status_code != 200:
            print("TENDER FAIL:", r.status_code)
            return None

        html = r.text

        # 2. izvuci pravi entityId (660xxx)
        match = re.search(r"entityId=(\d+)", html)

        if not match:
            print("NO ENTITY ID:", tender_id)
            return None

        entity_id = match.group(1)

        print("ENTITY:", entity_id)

        # 3. pozovi get-documents API
        api_url = f"{BASE_URL}/get-documents?entityId={entity_id}&objectMetaId=2&documentGroupId=169&associationTypeId=1"

        r2 = requests.get(api_url, headers=HEADERS, timeout=30)

        if r2.status_code != 200:
            print("DOC FAIL:", r2.status_code)
            return None

        data = r2.json()

        for doc in data:
            url = doc.get("DocumentUrl")

            if not url:
                continue

            full = BASE_URL + url

            pdf = requests.get(full, headers=HEADERS, timeout=60)

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

    except Exception as e:
        print("ERROR:", e)
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
# PRICES
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

        except:
            continue

    prices = sorted(set(prices))

    if prices:
        max_price = max(prices)
        prices = [p for p in prices if p > max_price * 0.2]

    print("PRICES:", prices)

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
# LOSS
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
