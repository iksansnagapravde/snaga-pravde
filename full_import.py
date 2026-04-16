import requests
import os
import re
import pdfplumber
import statistics
import json
import sqlite3
from datetime import datetime

BASE_URL = "https://jnportal.ujn.gov.rs"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

SAVE_FOLDER = "documents"
os.makedirs(SAVE_FOLDER, exist_ok=True)


# =========================================
# DATABASE
# =========================================
conn = sqlite3.connect("contracts.db")
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS tenders (
    entity_id INTEGER PRIMARY KEY,
    lowest REAL,
    average REAL,
    accepted REAL,
    loss_low REAL,
    loss_avg REAL,
    created_at TEXT
)
""")

conn.commit()


# =========================================
# DOWNLOAD
# =========================================
def download_pdf(entity_id):
    url = f"{BASE_URL}/GetDocuments.ashx?entityId={entity_id}"

    try:
        r = requests.get(url, headers=HEADERS, timeout=60)

        if r.status_code == 200 and len(r.content) > 2000:
            path = os.path.join(SAVE_FOLDER, f"{entity_id}.pdf")

            with open(path, "wb") as f:
                f.write(r.content)

            return path

    except:
        pass

    return None


# =========================================
# TEXT
# =========================================
def extract_text(pdf_path):
    text = ""

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"
    except:
        return ""

    return text


# =========================================
# SMART PRICES
# =========================================
def extract_prices(text):
    prices = []

    lines = text.split("\n")

    keywords = ["понуда", "вредност", "износ", "динара", "рсд"]

    for line in lines:
        if any(k in line.lower() for k in keywords):
            matches = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", line)

            for m in matches:
                try:
                    prices.append(float(m.replace(".", "").replace(",", ".")))
                except:
                    pass

    return prices


# =========================================
# ACCEPTED
# =========================================
def find_accepted(text):
    lines = text.split("\n")

    for i, line in enumerate(lines):
        if "изабрана" in line.lower():
            for j in range(i, i + 5):
                if j < len(lines):
                    m = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", lines[j])
                    if m:
                        return float(m[0].replace(".", "").replace(",", "."))

    return None


# =========================================
# ANALYZE
# =========================================
def analyze(prices, accepted):
    if len(prices) < 2:
        return None

    lowest = min(prices)
    avg = statistics.mean(prices)

    if not accepted:
        accepted = prices[-1]

    return lowest, avg, accepted, accepted - lowest, accepted - avg


# =========================================
# CHECK EXIST
# =========================================
def exists(entity_id):
    c.execute("SELECT 1 FROM tenders WHERE entity_id=?", (entity_id,))
    return c.fetchone() is not None


# =========================================
# SAVE
# =========================================
def save(entity_id, data):
    c.execute("""
    INSERT OR IGNORE INTO tenders
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        entity_id,
        data[0],
        data[1],
        data[2],
        data[3],
        data[4],
        datetime.now().isoformat()
    ))
    conn.commit()


# =========================================
# STATS
# =========================================
def write_stats():
    c.execute("SELECT COUNT(*), SUM(loss_low), SUM(loss_avg) FROM tenders")
    count, loss_low, loss_avg = c.fetchone()

    result = {
        "broj_analiziranih": count or 0,
        "gubitak_prema_najboljoj": loss_low or 0,
        "gubitak_prema_srednjoj": loss_avg or 0
    }

    with open("stats.json", "w") as f:
        json.dump(result, f, indent=2)


# =========================================
# ENTITY IDS (PRIVREMENO)
# =========================================
def get_ids():
    return [
        667697,
        668108,
        669001,
        657421,
        665276,
        665277,
        665278
    ]


# =========================================
# MAIN
# =========================================
def main():
    ids = get_ids()

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
    print("DONE")


if __name__ == "__main__":
    main()
