import os
import re
import json
import statistics
import sqlite3
from datetime import datetime
import time

import requests
from pdfminer.high_level import extract_text

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

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
# 🔥 FETCH IDS (STABILNO)
# =========================
def fetch_entity_ids():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=options)

    try:
        driver.get("https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora")
        time.sleep(6)

        links = driver.find_elements("xpath", "//a[contains(@href, '/tender-eo/')]")

        ids = []

        for l in links:
            href = l.get_attribute("href")

            if not href:
                continue

            match = re.search(r'/tender-eo/(\d+)', href)

            if match:
                eid = int(match.group(1))

                if not exists(eid):
                    ids.append(eid)

        ids = list(dict.fromkeys(ids))

        print("NEW IDS:", ids[:10])
        return ids[:100]

    finally:
        driver.quit()

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
# TEXT
# =========================
def extract_text_safe(pdf_path):
    try:
        text = extract_text(pdf_path)
        if text and len(text) > 200:
            return text
    except:
        pass
    return ""

# =========================
# BIDS
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

                if price < 10000 or price > 1_000_000_000:
                    continue

                bids.append({
                    "company": line[:80].strip(),
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
# EXPORT
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
# STATS
# =========================
def write_stats():
    c.execute("SELECT lowest, accepted FROM tenders")
    rows = c.fetchall()

    kurs = 117.2

    total_lowest = 0
    total_accepted = 0
    valid = 0

    for lowest, accepted in rows:
        if lowest and accepted and lowest > 0 and accepted > 0:
            total_lowest += lowest
            total_accepted += accepted
            valid += 1

    stats = {
        "broj_tendera": valid,
        "ukupna_vrednost": f"{round(total_lowest, 2)} RSD",
        "ukupna_vrednost_eur": f"{round(total_lowest / kurs, 2)} EUR",
        "broj_ugovora": valid,
        "ugovorena_vrednost": f"{round(total_accepted, 2)} RSD",
        "ugovorena_vrednost_eur": f"{round(total_accepted / kurs, 2)} EUR"
    }

    with open("stats.json", "w") as f:
        json.dump(stats, f, indent=2)

# =========================
# LOSS
# =========================
def write_loss_data():
    c.execute("SELECT lowest, median, accepted FROM tenders")
    rows = c.fetchall()

    total_low = total_med = total_acc = 0
    loss_low = loss_med = 0
    valid = 0

    for lowest, median, accepted in rows:
        if lowest and accepted and lowest > 0 and accepted > 0:
            valid += 1
            total_low += lowest
            total_acc += accepted
            total_med += median if median else 0

            loss_low += max(0, accepted - lowest)
            if median:
                loss_med += max(0, accepted - median)

    data = {
        "najbolja_ponuda": round(total_low, 2),
        "medijana_ponuda": round(total_med, 2),
        "prihvacena_ponuda": round(total_acc, 2),
        "broj_analiziranih": valid,
        "gubitak_prema_najboljoj": round(loss_low, 2),
        "gubitak_prema_medijani": round(loss_med, 2),
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
