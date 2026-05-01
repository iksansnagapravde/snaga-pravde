import os
import re
import json
import statistics
import sqlite3
from datetime import datetime

from pdf2image import convert_from_path
import pytesseract

# =========================
# SETUP
# =========================
os.makedirs("documents", exist_ok=True)

# 🔥 reset baze svaki put (rešava tvoj problem)
if os.path.exists("contracts.db"):
    os.remove("contracts.db")

conn = sqlite3.connect("contracts.db")
c = conn.cursor()

c.execute("""
CREATE TABLE tenders (
    entity_id INTEGER,
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
# OCR
# =========================
def extract_text(pdf_path):
    text = ""

    try:
        images = convert_from_path(pdf_path, dpi=300)

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
    if not prices:
        return None

    lowest = min(prices)
    medijana = statistics.median(prices) if len(prices) > 1 else lowest

    if not accepted:
        accepted = prices[-1]

    loss_low = max(0, accepted - lowest)
    loss_med = max(0, accepted - medijana)

    return lowest, medijana, accepted, loss_low, loss_med

# =========================
# PROCESS FILES
# =========================
def process_files():
    files = os.listdir("documents")

    if not files:
        print("NEMA PDF FAJLOVA")
        return

    for i, file in enumerate(files):

        if not file.lower().endswith(".pdf"):
            continue

        path = os.path.join("documents", file)

        print("PROCESS:", file)

        text = extract_text(path)

        if len(text) < 100:
            print("PRAZAN PDF")
            continue

        prices = extract_prices(text)
        accepted = find_accepted(text)

        result = analyze(prices, accepted)

        if result:
            c.execute("""
            INSERT INTO tenders VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (i, *result, datetime.now().isoformat()))

    conn.commit()

# =========================
# STATS
# =========================
def write_stats():
    c.execute("SELECT lowest, accepted FROM tenders")
    rows = c.fetchall()

    kurs = 117.2

    total_lowest = sum(r[0] for r in rows)
    total_accepted = sum(r[1] for r in rows)

    stats = {
        "broj_tendera": len(rows),
        "ukupna_vrednost": total_lowest,
        "ukupna_vrednost_eur": round(total_lowest / kurs, 2),
        "ugovorena_vrednost": total_accepted,
        "ugovorena_vrednost_eur": round(total_accepted / kurs, 2)
    }

    with open("stats.json", "w") as f:
        json.dump(stats, f, indent=2)

# =========================
# LOSS
# =========================
def write_loss_data():
    c.execute("SELECT lowest, medijana, accepted, loss_low, loss_medijana FROM tenders")
    rows = c.fetchall()

    data = {
        "najbolja_ponuda": sum(r[0] for r in rows),
        "medijana_ponuda": sum(r[1] for r in rows),
        "prihvacena_ponuda": sum(r[2] for r in rows),
        "broj_analiziranih": len(rows),
        "gubitak_prema_najboljoj": sum(r[3] for r in rows),
        "gubitak_prema_medijani": sum(r[4] for r in rows)
    }

    with open("loss-data.json", "w") as f:
        json.dump(data, f, indent=2)

# =========================
# MAIN
# =========================
def main():
    process_files()
    write_stats()
    write_loss_data()
    print("DONE")

if __name__ == "__main__":
    main()
