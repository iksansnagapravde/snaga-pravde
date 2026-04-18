import os
import re
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
# SESSION
# =========================
def get_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": BASE_URL
    })
    return s

# =========================
# RETRY HELPER
# =========================
def safe_request(func, retries=3, delay=2):
    for _ in range(retries):
        try:
            r = func()
            if r.status_code == 200:
                return r
        except:
            pass
        time.sleep(delay)
    return None

# =========================
# FETCH IDS
# =========================
def fetch_ids(session, page):
    payload = {"page": page, "pageSize": 50, "filters": []}

    r = safe_request(lambda: session.post(SEARCH_URL, json=payload, timeout=30))
    if not r:
        return []

    data = r.json()
    items = data.get("items") or data.get("Data") or []

    return [
        i.get("entityId") or i.get("EntityId")
        for i in items if i.get("entityId") or i.get("EntityId")
    ]

# =========================
# DOWNLOAD PDF (SMART)
# =========================
def download_pdf(session, eid):
    params = {
        "entityId": eid,
        "objectMetaId": 2,
        "documentGroupId": 169,
        "associationTypeId": 1
    }

    r = safe_request(lambda: session.get(DOCS_URL, params=params, timeout=30))
    if not r:
        return None

    try:
        docs = r.json()
    except:
        return None

    for doc in docs:
        name = (doc.get("name") or "").lower()

        # 🔥 uzmi samo odluke
        if not any(k in name for k in ["odluka", "dodel", "ponuda"]):
            continue

        url = doc.get("url") or doc.get("DocumentUrl")
        if not url:
            continue

        full = BASE_URL + url
        pdf = safe_request(lambda: session.get(full, timeout=60))
        if not pdf:
            continue

        if not pdf.content.startswith(b"%PDF"):
            continue

        path = f"{DOCUMENTS_DIR}/{eid}.pdf"
        with open(path, "wb") as f:
            f.write(pdf.content)

        return path

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

    # OCR fallback (limited pages)
    if len(text) < 200:
        try:
            images = convert_from_path(pdf, dpi=200, first_page=1, last_page=3)
            for img in images:
                text += pytesseract.image_to_string(img, lang="srp+eng")
        except:
            pass

    return text

# =========================
# PRICE EXTRACTION (SMARTER)
# =========================
def extract_prices(text):
    prices = []

    for line in text.split("\n"):
        if not any(w in line.lower() for w in ["ponuda", "cena", "ukupno"]):
            continue

        matches = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", line)

        for m in matches:
            try:
                val = float(m.replace(".", "").replace(",", "."))
                if 500000 < val < 1_000_000_000:
                    prices.append(val)
            except:
                pass

    return sorted(set(prices))

# =========================
# ACCEPTED PRICE
# =========================
def find_accepted(text):
    keywords = [
        "izabrana ponuda",
        "najpovoljnija",
        "dodeljuje se",
        "odluka o dodeli"
    ]

    for line in text.split("\n"):
        if any(k in line.lower() for k in keywords):
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
        accepted = med  # 🔥 bolja pretpostavka

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
    INSERT OR REPLACE INTO tenders VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (eid, *data, datetime.now().isoformat()))
    conn.commit()

# =========================
# CLEANUP
# =========================
def cleanup(path):
    try:
        os.remove(path)
    except:
        pass

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

            cleanup(pdf)

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
