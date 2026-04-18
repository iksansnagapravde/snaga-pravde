import os
import re
import json
import time
import statistics
import sqlite3
from datetime import datetime

import requests
from pypdf import PdfReader
from pdf2image import convert_from_path
import pytesseract
from tqdm import tqdm

# =========================
# CONFIG
# =========================
BASE_URL = "https://jnportal.ujn.gov.rs"
SEARCH_URL = f"{BASE_URL}/api/Search/GetSearchResults"
DOCS_URL = f"{BASE_URL}/get-documents"

DOCUMENTS_DIR = "documents"
os.makedirs(DOCUMENTS_DIR, exist_ok=True)

# =========================
# DB
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
# SESSION (NO COOKIES)
# =========================
def get_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*"
    })
    return s

# =========================
# FETCH IDS (API)
# =========================
def fetch_ids(session, page):
    payload = {
        "page": page,
        "pageSize": 50,
        "filters": []
    }

    try:
        r = session.post(SEARCH_URL, json=payload, timeout=30)
        if r.status_code != 200:
            return []

        data = r.json()
        items = data.get("items") or data.get("Data") or []

        ids = []
        for i in items:
            eid = i.get("entityId") or i.get("EntityId")
            if eid:
                ids.append(eid)

        return ids

    except:
        return []

# =========================
# DOWNLOAD PDF
# =========================
def download_pdf(session, eid):
    params = {
        "entityId": eid,
        "objectMetaId": 2,
        "documentGroupId": 169,
        "associationTypeId": 1
    }

    try:
        r = session.get(DOCS_URL, params=params, timeout=30)
        docs = r.json()

        for doc in docs:
            url = doc.get("url") or doc.get("DocumentUrl")
            if not url:
                continue

            full = BASE_URL + url

            pdf = session.get(full, timeout=60)

            if pdf.status_code != 200:
                continue

            if not pdf.content.startswith(b"%PDF"):
                continue

            path = f"{DOCUMENTS_DIR}/{eid}.pdf"

            with open(path, "wb") as f:
                f.write(pdf.content)

            return path

    except:
        return None

    return None

# =========================
# TEXT EXTRACTION
# =========================
def extract_text(pdf):
    text = ""

    try:
        reader = PdfReader(pdf)
        for p in reader.pages:
            text += p.extract_text() or ""
    except:
        pass

    if len(text) < 100:
        try:
            images = convert_from_path(pdf, dpi=250)
            for img in images:
                text += pytesseract.image_to_string(img)
        except:
            pass

    return text

# =========================
# PRICE EXTRACTION
# =========================
def extract_prices(text):
    matches = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", text)

    prices = []
    for m in matches:
        try:
            val = float(m.replace(".", "").replace(",", "."))
            if 500000 < val < 1_000_000_000:
                prices.append(val)
        except:
            pass

    prices = sorted(set(prices))

    if prices:
        maxp = max(prices)
        prices = [p for p in prices if p > maxp * 0.2]

    return prices

# =========================
# ACCEPTED PRICE
# =========================
def find_accepted(text):
    for line in text.split("\n"):
        if "изабрана" in line.lower() or "најповољнија" in line.lower():
            m = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", line)
            if m:
                try:
                    return float(m[0].replace(".", "").replace(",", "."))
                except:
                    pass
    return None

# =========================
# ANALYZE
# =========================
def analyze(prices, accepted):
    if not prices:
        return None

    low = min(prices)
    med = statistics.median(prices)

    if not accepted:
        accepted = max(prices)

    return (
        low,
        med,
        accepted,
        max(0, accepted - low),
        max(0, accepted - med)
    )

# =========================
# SAVE
# =========================
def save(eid, data):
    c.execute("""
    INSERT OR IGNORE INTO tenders VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (eid, *data, datetime.now().isoformat()))
    conn.commit()

# =========================
# MAIN LOOP
# =========================
def run():
    session = get_session()
    page = 1

    while True:
        print(f"\nPAGE {page}")

        ids = fetch_ids(session, page)
        if not ids:
            break

        for eid in tqdm(ids):
            if c.execute("SELECT 1 FROM tenders WHERE entity_id=?", (eid,)).fetchone():
                continue

            pdf = download_pdf(session, eid)
            if not pdf:
                continue

            text = extract_text(pdf)
            prices = extract_prices(text)
            accepted = find_accepted(text)

            result = analyze(prices, accepted)
            if result:
                save(eid, result)

        page += 1
        time.sleep(1)

# =========================
# AUTO LOOP
# =========================
if __name__ == "__main__":
    while True:
        run()
        print("WAIT 5 MIN")
        time.sleep(300)
