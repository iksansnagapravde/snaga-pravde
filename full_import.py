import os
import json
import re
import sqlite3
import statistics
from datetime import datetime
from io import BytesIO

import requests
from docx import Document


# =====================================================
# CONFIG
# =====================================================

BASE_URL = "https://jnportal.ujn.gov.rs"
HOME_URL = BASE_URL + "/"
API_URL = BASE_URL + "/api/odluke-o-dodeli-ugovora/pretraga"

DB_FILE = "contracts.db"
STATS_FILE = "stats.json"

EUR_RATE = 117.2
MAX_ROWS_PER_RUN = 20
REQUEST_TIMEOUT = 60


# =====================================================
# DB
# =====================================================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS tenders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tender_key TEXT UNIQUE,
        title TEXT,
        supplier TEXT,
        publish_date TEXT,
        budget_value REAL DEFAULT 0,
        lowest_bid REAL DEFAULT 0,
        median_bid REAL DEFAULT 0,
        accepted_bid REAL DEFAULT 0,
        bidder_count INTEGER DEFAULT 0,
        raw_text TEXT
    )
    """)

    conn.commit()
    conn.close()


# =====================================================
# HELPERS
# =====================================================

def money_to_float(value):
    if not value:
        return 0.0

    cleaned = str(value).replace("\xa0", "").strip()
    cleaned = re.sub(r"[^\d,.\-]", "", cleaned)

    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")

    try:
        return float(cleaned)
    except:
        return 0.0


def format_rsd(value):
    return f"{round(value):,}".replace(",", ".") + " RSD"


def format_eur(value):
    return f"{round(value):,}".replace(",", ".") + " EUR"


# =====================================================
# DOCX READER
# =====================================================

def extract_docx_text_from_bytes(content):
    try:
        doc = Document(BytesIO(content))

        paragraphs = []
        for p in doc.paragraphs:
            txt = p.text.strip()
            if txt:
                paragraphs.append(txt)

        return "\n".join(paragraphs)

    except Exception as e:
        print("DOCX ERROR:", e)
        return ""


# =====================================================
# PARSERS
# =====================================================

def parse_budget_value(text):
    patterns = [
        r"Процењена вредност предмета.*?:\s*([\d\.\,]+)",
        r"Процењена вредност набавке.*?:\s*([\d\.\,]+)",
        r"Procenjena vrednost predmeta.*?:\s*([\d\.\,]+)",
        r"Procenjena vrednost nabavke.*?:\s*([\d\.\,]+)",
    ]

    for p in patterns:
        m = re.search(p, text, re.IGNORECASE | re.DOTALL)
        if m:
            return money_to_float(m.group(1))

    return 0.0


def parse_accepted_bid(text):
    patterns = [
        r"Вредност уговора без ПДВ[:\s]+([\d\.\,]+)",
        r"Vrednost ugovora bez PDV[:\s]+([\d\.\,]+)",
    ]

    for p in patterns:
        m = re.search(p, text, re.IGNORECASE | re.DOTALL)
        if m:
            return money_to_float(m.group(1))

    return 0.0


def parse_supplier(text):
    patterns = [
        r"Уговор се додељује привредном субјекту[:\s]+(.+?)(?:\n|ПИБ)",
        r"Ugovor se dodeljuje privrednom subjektu[:\s]+(.+?)(?:\n|PIB)",
    ]

    for p in patterns:
        m = re.search(p, text, re.IGNORECASE | re.DOTALL)
        if m:
            return " ".join(m.group(1).split())

    return ""


def parse_bid_prices(text):
    prices = []
    found = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", text)

    for val in found:
        num = money_to_float(val)
        if num > 0:
            prices.append(num)

    return sorted(prices)


# =====================================================
# AUTH SESSION API FETCH
# =====================================================

def fetch_decisions():
    print("=" * 50)
    print("FETCHING API DATA WITH SESSION")
    print("=" * 50)

    session = requests.Session()

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json"
    }

    # STEP 1: open homepage to get cookies
    home = session.get(HOME_URL, headers=headers, timeout=REQUEST_TIMEOUT)
    print("HOME STATUS:", home.status_code)

    payload = {
        "page": 1,
        "pageSize": MAX_ROWS_PER_RUN
    }

    # STEP 2: call API with session cookies
    r = session.post(
        API_URL,
        json=payload,
        headers=headers,
        timeout=REQUEST_TIMEOUT
    )

    print("API STATUS:", r.status_code)

    if r.status_code != 200:
        print(r.text[:500])
        return []

    data = r.json()

    if isinstance(data, dict):
        if "items" in data:
            return data["items"]
        if "data" in data:
            return data["data"]

    return []


# =====================================================
# DOWNLOAD DOCX
# =====================================================

def download_docx(url):
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            return r.content
    except Exception as e:
        print("DOWNLOAD ERROR:", e)

    return None


# =====================================================
# DB SAVE
# =====================================================

def save_tender(record):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    INSERT OR IGNORE INTO tenders (
        tender_key,title,supplier,publish_date,
        budget_value,lowest_bid,median_bid,
        accepted_bid,bidder_count,raw_text
    ) VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        record["tender_key"],
        record["title"],
        record["supplier"],
        record["publish_date"],
        record["budget_value"],
        record["lowest_bid"],
        record["median_bid"],
        record["accepted_bid"],
        record["bidder_count"],
        record["raw_text"]
    ))

    conn.commit()
    conn.close()


# =====================================================
# OUTPUTS
# =====================================================

def write_outputs():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    SELECT COUNT(*),
           COALESCE(SUM(budget_value),0),
           COALESCE(SUM(accepted_bid),0)
    FROM tenders
    """)
    row = c.fetchone()
    conn.close()

    total_tenders = row[0]
    total_budget = row[1]
    total_accepted = row[2]

    stats = {
        "broj_tendera": total_tenders,
        "ukupna_vrednost": format_rsd(total_budget),
        "ukupna_vrednost_eur": format_eur(total_budget / EUR_RATE),
        "broj_ugovora": total_tenders,
        "ugovorena_vrednost": format_rsd(total_accepted),
        "ugovorena_vrednost_eur": format_eur(total_accepted / EUR_RATE)
    }

    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


# =====================================================
# MAIN
# =====================================================

def main():
    init_db()

    decisions = fetch_decisions()

    print("FOUND DECISIONS:", len(decisions))

    for idx, item in enumerate(decisions):
        try:
            docx_url = item.get("dokumentUrl") or item.get("docxUrl")

            if not docx_url:
                continue

            if docx_url.startswith("/"):
                docx_url = BASE_URL + docx_url

            content = download_docx(docx_url)

            if not content:
                continue

            doc_text = extract_docx_text_from_bytes(content)

            if not doc_text:
                continue

            supplier = parse_supplier(doc_text)
            budget_value = parse_budget_value(doc_text)
            accepted_bid = parse_accepted_bid(doc_text)

            prices = parse_bid_prices(doc_text)

            if prices:
                lowest_bid = min(prices)
                median_bid = statistics.median(prices)
                bidder_count = len(prices)
            else:
                lowest_bid = 0
                median_bid = 0
                bidder_count = 0

            record = {
                "tender_key": f"{supplier}_{idx}_{datetime.now()}",
                "title": item.get("nazivPredmetaNabavke", f"Tender {idx+1}"),
                "supplier": supplier,
                "publish_date": datetime.today().strftime("%d.%m.%Y"),
                "budget_value": budget_value,
                "lowest_bid": lowest_bid,
                "median_bid": median_bid,
                "accepted_bid": accepted_bid,
                "bidder_count": bidder_count,
                "raw_text": doc_text[:20000]
            }

            save_tender(record)

            print("SAVED:", supplier)

        except Exception as e:
            print("ERROR:", e)

    write_outputs()

    print("=" * 50)
    print("DONE")
    print("stats.json updated")
    print("=" * 50)


if __name__ == "__main__":
    main()
