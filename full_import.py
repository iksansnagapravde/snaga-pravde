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
    average REAL,
    accepted REAL,
    loss_low REAL,
    loss_avg REAL,
    created_at TEXT
)
""")
conn.commit()


# =========================================
# FETCH ENTITY IDs (AUTO)
# =========================================
def fetch_entity_ids():
    try:
        url = BASE_URL + "/get-documents"
        r = requests.get(url, headers=HEADERS)
        data = r.json()

        ids = []
        for item in data:
            if "LotId" in item:
                ids.append(item["LotId"])

        return ids

    except Exception as e:
        print("FETCH ERROR:", e)
        return []


# =========================================
# DOWNLOAD PDF
# =========================================
def download_pdf(entity_id):
    url = f"{BASE_URL}/GetDocuments.ashx?entityId={entity_id}"

    try:
        r = requests.get(url, headers=HEADERS, timeout=60)

        if r.status_code == 200 and len(r.content) > 2000:
            path = f"documents/{entity_id}.pdf"

            with open(path, "wb") as f:
                f.write(r.content)

            return path

    except Exception as e:
        print("DOWNLOAD ERROR:", e)

    return None


# =========================================
# EXTRACT TEXT
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
# EXTRACT PRICES (SMART)
# =========================================
def extract_prices(text):
    prices = []

    for line in text.split("\n"):
        if any(k in line.lower() for k in ["понуда", "вредност", "динара", "рсд"]):
            matches = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", line)

            for m in matches:
                try:
                    prices.append(float(m.replace(".", "").replace(",", ".")))
                except:
                    pass

    return prices


# =========================================
# FIND ACCEPTED PRICE
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
# ANALYSIS
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
# CHECK EXISTS
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
# WRITE STATS
# =========================================
def write_stats():
    c.execute("SELECT COUNT(*), SUM(loss_low), SUM(loss_avg) FROM tenders")
    count, loss_low, loss_avg = c.fetchone()

    stats = {
        "broj_analiziranih": count or 0,
        "gubitak_prema_najboljoj": loss_low or 0,
        "gubitak_prema_srednjoj": loss_avg or 0,
        "timestamp": datetime.now().isoformat()
    }

    with open("stats.json", "w") as f:
        json.dump(stats, f, indent=2)


# =========================================
# WRITE LOSS DATA (DETALJI)
# =========================================
def write_loss_data():
    c.execute("SELECT entity_id, loss_low, loss_avg FROM tenders")

    rows = c.fetchall()

    data = []
    for r in rows:
        data.append({
            "entity_id": r[0],
            "loss_low": r[1],
            "loss_avg": r[2]
        })

    with open("loss-data.json", "w") as f:
        json.dump(data, f, indent=2)


# =========================================
# MAIN
# =========================================
def main():
    ids = fetch_entity_ids()

    print("FOUND IDS:", len(ids))

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
