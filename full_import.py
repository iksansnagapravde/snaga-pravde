import requests
import os
import re
import pdfplumber
import statistics
import json
import sqlite3
from datetime import datetime

BASE_URL = "https://jnportal.ujn.gov.rs"

# 🔥 FULL BROWSER HEADERS (rešava 401)
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "sr-RS,sr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora",
    "Origin": "https://jnportal.ujn.gov.rs",
    "Connection": "keep-alive"
}

# 🔥 SESSION (KLJUČNO)
session = requests.Session()
session.headers.update(HEADERS)

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

        if "medijana" not in columns or "loss_medijana" not in columns:
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
# FETCH IDS (FINAL)
# =========================================
def fetch_entity_ids():
    ids = []

    try:
        for page in range(0, 50):
            skip = page * 10

            url = f"{BASE_URL}/api/searchgrid/VAwardDecisions/get?skip={skip}&take=10"

            r = session.get(url)

            if r.status_code != 200:
                print("BAD STATUS:", r.status_code)
                continue

            data = r.json()

            if not data or "data" not in data or not data["data"]:
                break

            for item in data["data"]:
                if "Id" in item:
                    ids.append(item["Id"])

        ids = list(set(ids))

        print("FOUND IDS:", ids)

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
        r = session.get(url, timeout=60)

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
# EXTRACT PRICES
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
