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
# FETCH IDS
# =========================
def fetch_entity_ids():
    url = f"{BASE_URL}/odluke-o-dodeli-ugovora"

    r = requests.get(url, headers=HEADERS)
    html = r.text

    found = re.findall(r"/tender-eo/(\d+)", html)
    found = list(dict.fromkeys(found))

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
# TEXT EXTRACTION
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

                # filtriranje smeća
                if price < 10000 or price > 1_000_000_000:
                    continue

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
    if len(bids) < 2:
        return None

    prices = [b["price"] for b in bids]

    lowest = min(prices)
    median = statistics.median(prices)

    # 👉 privremeno: accepted = lowest (realnije nego max)
    accepted = lowest

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
# EXPORT JSON (debug)
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
# WRITE STATS
# =========================
def write_stats():
    c.execute("SELECT lowest, accepted FROM tenders")
    rows = c.fetchall()

    kurs = 117.2

    total_lowest = 0
    total_accepted = 0
    valid_count = 0

    for r in rows:
        lowest, accepted = r

        if lowest is None or accepted is None:
            continue

        if lowest <= 0 or accepted <= 0:
            continue

        valid_count += 1

        total_lowest += lowest
        total_accepted += accepted

    stats = {
        "broj_tendera": valid_count,
        "ukupna_vrednost": f"{round(total_lowest, 2)} RSD",
        "ukupna_vrednost_eur": f"{round(total_lowest / kurs, 2)} EUR",
        "broj_ugovora": valid_count,
        "ugovorena_vrednost": f"{round(total_accepted, 2)} RSD",
        "ugovorena_vrednost_eur": f"{round(total_accepted / kurs, 2)} EUR"
    }

    with open("stats.json", "w") as f:
        json.dump(stats, f, indent=2)

# =========================
# WRITE LOSS
# =========================
def write_loss_data():
    c.execute("SELECT lowest, median, accepted FROM tenders")
    rows = c.fetchall()

    total_lowest = 0
    total_median = 0
    total_accepted = 0

    loss_low_total = 0
    loss_med_total = 0

    valid_count = 0

    for r in rows:
        lowest, median, accepted = r

        if lowest is None or accepted is None:
            continue

        if lowest <= 0 or accepted <= 0:
            continue

        valid_count += 1

        total_lowest += lowest
        total_accepted += accepted
        total_median += median if median is not None else 0

        loss_low = max(0, accepted - lowest)

        if median is not None:
            loss_med = max(0, accepted - median)
        else:
            loss_med = 0

        loss_low_total += loss_low
        loss_med_total += loss_med

    data = {
        "najbolja_ponuda": round(total_lowest, 2),
        "medijana_ponuda": round(total_median, 2),
        "prihvacena_ponuda": round(total_accepted, 2),
        "broj_analiziranih": valid_count,
        "gubitak_prema_najboljoj": round(loss_low_total, 2),
        "gubitak_prema_medijani": round(loss_med_total, 2),
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

        text = extract_text_safe(pdf)

        if len(text) < 100:
            continue

        bids = extract_bids(text)

        if len(bids) < 2:
            continue

        analysis = analyze(bids)

        if analysis:
            save(eid, bids, analysis)

    export_json()
    write_stats()
    write_loss_data()

    print("DONE")

if __name__ == "__main__":
    main()
