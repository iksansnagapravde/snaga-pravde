import os
import re
import json
import statistics
import sqlite3
from datetime import datetime

import requests
from pdf2image import convert_from_path
import pytesseract

# =========================
# CONFIG
# =========================

BASE_URL = "https://jnportal.ujn.gov.rs"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*"
}

os.makedirs("documents", exist_ok=True)

# =========================
# DATABASE
# =========================

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
# TEST IDS (MVP)
# =========================

def fetch_entity_ids():
    return [675152, 670413, 666041]

# =========================
# DOWNLOAD PDF
# =========================

def download_pdf(eid):
    try:
        meta_url = f"{BASE_URL}/get-documents?entityId={eid}&objectMetaId=2&documentGroupId=169&associationTypeId=1"

        r = requests.get(meta_url, headers=HEADERS)

        print("META STATUS:", r.status_code)

        if r.status_code != 200:
            return None

        data = r.json()

        if not data:
            print("NO DOCUMENTS")
            return None

        file_url = data[0].get("url") or data[0].get("downloadUrl")

        if not file_url:
            print("NO FILE URL")
            return None

        if file_url.startswith("/"):
            file_url = BASE_URL + file_url

        pdf = requests.get(file_url, headers=HEADERS)

        print("PDF STATUS:", pdf.status_code)

        if pdf.status_code != 200:
            return None

        filename = f"documents/{eid}.pdf"

        with open(filename, "wb") as f:
            f.write(pdf.content)

        print("DOWNLOADED:", filename)

        return filename

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
            t = pytesseract.image_to_string(
                img,
                lang="eng",  # ako imaš srp može "srp"
                config="--oem 3 --psm 6"
            )
            text += t + "\n"

    except Exception as e:
        print("OCR ERROR:", e)

    return text

# =========================
# CLEAN TEXT
# =========================

def clean_text(text):
    text = text.replace("\x0c", " ")
    text = re.sub(r"\s+", " ", text)
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

            if num < 10000:
                continue

            if num > 1_000_000_000:
                continue

            prices.append(num)

        except:
            continue

    prices = sorted(set(prices))

    print("FINAL PRICES:", prices)

    return prices

# =========================
# ACCEPTED
# =========================

def find_accepted(text):
    patterns = [
        "вредност уговора",
        "уговорена вредност",
        "износ уговора"
    ]

    for part in text.split("."):
        for p in patterns:
            if p in part.lower():
                m = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", part)
                if m:
                    return float(m[0].replace(".", "").replace(",", "."))

    return None

# =========================
# WINNER
# =========================

def find_winner(text):
    parts = text.split(".")

    for i, part in enumerate(parts):
        if "додељује" in part.lower():
            for j in range(i, i + 3):
                if j < len(parts):
                    if "доо" in parts[j].lower() or "a.d." in parts[j].lower():
                        return parts[j].strip()

    return None

# =========================
# ANALYZE
# =========================

def analyze(prices, accepted):
    if not prices:
        return None

    lowest = min(prices)
    medijana = statistics.median(prices)

    if not accepted:
        accepted = prices[-1]

    loss_low = max(0, accepted - lowest)
    loss_med = max(0, accepted - medijana)

    return lowest, medijana, accepted, loss_low, loss_med

# =========================
# SAVE DB
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

    output = []

    for eid in ids:
        print("\nPROCESS:", eid)

        pdf = download_pdf(eid)
        if not pdf:
            continue

        text = extract_text(pdf)

        if len(text) < 100:
            print("TEXT TOO SHORT")
            continue

        text = clean_text(text)

        prices = extract_prices(text)
        accepted = find_accepted(text)
        winner = find_winner(text)

        result = analyze(prices, accepted)

        if result:
            lowest, medijana, accepted, loss_low, loss_med = result

            output.append({
                "id": eid,
                "winner": winner,
                "accepted": accepted,
                "lowest": lowest,
                "median": medijana,
                "loss_low": loss_low,
                "loss_median": loss_med
            })

            save(eid, result)

    # =========================
    # JSON OUTPUT
    # =========================

    with open("tenders.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print("\nDONE")

# =========================

if __name__ == "__main__":
    main()
