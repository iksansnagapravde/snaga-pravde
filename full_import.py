import os
import re
import json
import statistics
import sqlite3
from datetime import datetime

import requests
from pdfminer.high_level import extract_text

BASE_URL = "https://jnportal.ujn.gov.rs"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
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
    subject TEXT,
    winner TEXT,
    lowest REAL,
    median REAL,
    accepted REAL,
    risk INTEGER,
    bids_json TEXT,
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
# FETCH IDS (NO SELENIUM)
# =========================
def fetch_entity_ids():
    url = f"{BASE_URL}/odluke-o-dodeli-ugovora"

    r = requests.get(url, headers=HEADERS)
    html = r.text

    found = re.findall(r"/tender-eo/(\d+)", html)
    found = list(dict.fromkeys(found))

    # 🔥 uzmi više da ne promašiš
    found = found[:100]

    new_ids = []

    for eid in found:
        eid = int(eid)
        if not exists(eid):
            new_ids.append(eid)

    print("NEW IDS:", new_ids)
    return new_ids

# =========================
# DOWNLOAD PDF
# =========================
def download_pdf(tender_id):
    try:
        url = f"{BASE_URL}/tender-eo/{tender_id}"

        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code != 200:
            return None

        html = r.text

        match = re.search(r'"entityId"\s*:\s*(\d+)', html)
        if not match:
            return None

        entity_id = match.group(1)

        api_url = f"{BASE_URL}/get-documents?entityId={entity_id}&objectMetaId=2&documentGroupId=169&associationTypeId=1"

        r2 = requests.get(api_url, headers=HEADERS, timeout=30)
        if r2.status_code != 200:
            return None

        try:
            data = r2.json()
        except:
            return None

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

        return None

    except Exception as e:
        print("DOWNLOAD ERROR:", e)
        return None

# =========================
# PDF TEXT (NO OCR)
# =========================
def extract_text_safe(pdf_path):
    try:
        text = extract_text(pdf_path)

        if text and len(text) > 200:
            return text

    except Exception as e:
        print("TEXT ERROR:", e)

    return ""

# =========================
# EXTRACT BIDS
# =========================
def extract_bids(text):
    bids = []

    for line in text.split("\n"):

        if any(k in line.lower() for k in ["rsd", "динара"]):

            matches = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", line)

            if not matches:
                continue

            try:
                price = float(matches[0].replace(".", "").replace(",", "."))

                company = line[:80].strip()

                bids.append({
                    "company": company,
                    "price": price
                })

            except:
                continue

    print("BIDS:", bids)
    return bids

# =========================
# ANALYZE
# =========================
def analyze(bids):
    if len(bids) < 1:
        return None

    prices = [b["price"] for b in bids]

    lowest = min(prices)

    median = statistics.median(prices) if len(prices) >= 2 else lowest

    accepted = max(prices)  # fallback

    # 🔥 RISK SCORE
    risk = 0

    if accepted > lowest:
        risk += 3

    if accepted > median * 1.2:
        risk += 2

    if len(prices) <= 2:
        risk += 2

    return {
        "lowest": lowest,
        "median": median,
        "accepted": accepted,
        "risk": risk
    }

# =========================
# SAVE
# =========================
def save(eid, bids, analysis):
    c.execute("""
    INSERT OR REPLACE INTO tenders
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        eid,
        "",
        "",
        analysis["lowest"],
        analysis["median"],
        analysis["accepted"],
        analysis["risk"],
        json.dumps(bids),
        datetime.now().isoformat()
    ))
    conn.commit()

# =========================
# EXPORT JSON
# =========================
def export_json():
    c.execute("SELECT entity_id, lowest, median, accepted, risk FROM tenders")
    rows = c.fetchall()

    data = []

    for r in rows:
        data.append({
            "id": r[0],
            "lowest": r[1],
            "median": r[2],
            "accepted": r[3],
            "risk": r[4]
        })

    with open("tenders.json", "w") as f:
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

        text = extract_text_safe(pdf)

        if len(text) < 100:
            continue

        bids = extract_bids(text)

        if len(bids) < 1:
            continue

        analysis = analyze(bids)

        if analysis:
            save(eid, bids, analysis)

    export_json()

    print("DONE")

if __name__ == "__main__":
    main()
