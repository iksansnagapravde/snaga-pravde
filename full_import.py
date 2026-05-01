import os
import re
import json
import sqlite3
from pdfminer.high_level import extract_text

# =========================
# DB
# =========================
conn = sqlite3.connect("contracts.db")
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS tenders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT,
    winner TEXT,
    accepted REAL
)
""")
conn.commit()


# =========================
# PARSER (PARTIJE)
# =========================
def parse_tender(text):

    results = []

    parts = re.split(r"Број и назив партије:", text)

    for p in parts:

        winner_match = re.search(
            r"Уговор се додељује привредном субјекту:\s*(.+)",
            p
        )

        price_match = re.search(
            r"Вредност уговора \(са ПДВ\):\s*([\d\.\,]+)",
            p
        )

        if not winner_match or not price_match:
            continue

        winner = winner_match.group(1).strip()

        price_str = price_match.group(1)
        price = float(price_str.replace(".", "").replace(",", "."))

        results.append({
            "winner": winner,
            "accepted": price
        })

    return results


# =========================
# PROCESS PDF
# =========================
def process_files():

    for file in os.listdir("documents"):

        if not file.endswith(".pdf"):
            continue

        path = os.path.join("documents", file)

        print("PROCESS:", file)

        try:
            text = extract_text(path)

            data = parse_tender(text)

            for d in data:
                c.execute(
                    "INSERT INTO tenders (source_file, winner, accepted) VALUES (?, ?, ?)",
                    (file, d["winner"], d["accepted"])
                )

        except Exception as e:
            print("ERROR:", e)

    conn.commit()


# =========================
# EXPORT JSON
# =========================
def export_json():

    c.execute("SELECT winner, accepted FROM tenders")

    rows = c.fetchall()

    data = []

    for r in rows:
        data.append({
            "winner": r[0],
            "accepted": r[1]
        })

    with open("tenders.json", "w") as f:
        json.dump(data, f, indent=2)


# =========================
# MAIN
# =========================
def main():
    process_files()
    export_json()
    print("DONE")


if __name__ == "__main__":
    main()
