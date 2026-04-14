import json
import re
import sqlite3
import statistics
import time
from datetime import datetime
from io import BytesIO
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from docx import Document
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

# =====================================================
# CONFIG
# =====================================================

BASE_URL = "https://jnportal.ujn.gov.rs"
DECISIONS_URL = f"{BASE_URL}/odluke-o-dodeli-ugovora"

DB_FILE = "contracts.db"
STATS_FILE = "stats.json"
LOSS_FILE = "loss-data.json"
SUSPICIOUS_FILE = "suspicious-winners.json"

EUR_RATE = 117.2
REQUEST_TIMEOUT = 30
PAGE_WAIT_SECONDS = 5
MAX_ROWS_PER_RUN = 20

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


# =====================================================
# DRIVER
# =====================================================

def create_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1600,2200")
    return webdriver.Chrome(options=chrome_options)


# =====================================================
# DB INIT
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
        doc_url TEXT,
        detail_url TEXT,
        raw_text TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS suspicious_winners (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        supplier TEXT,
        title TEXT,
        publish_date TEXT,
        accepted_bid REAL,
        median_bid REAL,
        budget_loss REAL,
        doc_url TEXT UNIQUE,
        detail_url TEXT
    )
    """)

    conn.commit()
    conn.close()


# =====================================================
# MONEY PARSER
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

def extract_docx_text_from_url(doc_url):
    try:
        r = requests.get(doc_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()

        file_stream = BytesIO(r.content)
        doc = Document(file_stream)

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
# DECISION LIST
# =====================================================

def get_latest_decision_rows(driver):
    driver.get(DECISIONS_URL)
    time.sleep(PAGE_WAIT_SECONDS)

    soup = BeautifulSoup(driver.page_source, "html.parser")
    rows = soup.find_all("tr")

    parsed = []

    for row in rows:
        row_text = " ".join(row.get_text(" ", strip=True).split())

        if not row_text:
            continue

        links = row.find_all("a", href=True)

        doc_url = ""
        detail_url = ""

        for a in links:
            href = a["href"]
            full = urljoin(BASE_URL, href)

            if ".docx" in href.lower():
                doc_url = full

            if "/contract-eo/" in href:
                detail_url = full

        if doc_url:
            parsed.append({
                "row_text": row_text,
                "doc_url": doc_url,
                "detail_url": detail_url
            })

        if len(parsed) >= MAX_ROWS_PER_RUN:
            break

    print("FOUND ROWS:", len(parsed))
    return parsed


# =====================================================
# DB SAVE
# =====================================================

def tender_exists(key):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT 1 FROM tenders WHERE tender_key=?", (key,))
    exists = c.fetchone() is not None
    conn.close()
    return exists


def save_tender(record):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    INSERT OR REPLACE INTO tenders (
        tender_key,title,supplier,publish_date,
        budget_value,lowest_bid,median_bid,accepted_bid,
        bidder_count,doc_url,detail_url,raw_text
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
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
        record["doc_url"],
        record["detail_url"],
        record["raw_text"]
    ))

    conn.commit()
    conn.close()


def save_suspicious_winner(record):
    budget_loss = record["accepted_bid"] - record["median_bid"]

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    INSERT OR IGNORE INTO suspicious_winners (
        supplier,title,publish_date,
        accepted_bid,median_bid,budget_loss,
        doc_url,detail_url
    ) VALUES (?,?,?,?,?,?,?,?)
    """, (
        record["supplier"],
        record["title"],
        record["publish_date"],
        record["accepted_bid"],
        record["median_bid"],
        budget_loss,
        record["doc_url"],
        record["detail_url"]
    ))

    conn.commit()
    conn.close()


# =====================================================
# JSON EXPORT
# =====================================================

def write_outputs():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    SELECT COUNT(*),
           COALESCE(SUM(budget_value),0),
           COALESCE(SUM(lowest_bid),0),
           COALESCE(SUM(median_bid),0),
           COALESCE(SUM(accepted_bid),0)
    FROM tenders
    """)
    row = c.fetchone()

    total_tenders = row[0]
    total_budget = row[1]
    total_lowest = row[2]
    total_median = row[3]
    total_accepted = row[4]

    conn.close()

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
    driver = create_driver()

    rows = get_latest_decision_rows(driver)

    print("=" * 60)
    print("LATEST DECISIONS FOUND")
    print("=" * 60)

    for row in rows:
        row_text = row["row_text"]
        doc_url = row["doc_url"]
        detail_url = row["detail_url"]

        tender_key = row_text[:150] + "|" + doc_url

        if tender_exists(tender_key):
            print("SKIP:", row_text[:60])
            continue

        doc_text = extract_docx_text_from_url(doc_url)

        if not doc_text:
            print("EMPTY DOC:", doc_url)
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
            "tender_key": tender_key,
            "title": row_text[:250],
            "supplier": supplier,
            "publish_date": datetime.today().strftime("%d.%m.%Y"),
            "budget_value": budget_value,
            "lowest_bid": lowest_bid,
            "median_bid": median_bid,
            "accepted_bid": accepted_bid,
            "bidder_count": bidder_count,
            "doc_url": doc_url,
            "detail_url": detail_url,
            "raw_text": doc_text[:20000]
        }

        save_tender(record)

        if accepted_bid > median_bid and median_bid > 0:
            save_suspicious_winner(record)

        print("SAVED:", supplier, "| accepted:", accepted_bid)

    driver.quit()
    write_outputs()

    print("=" * 60)
    print("DONE")
    print("stats.json updated")
    print("=" * 60)


if __name__ == "__main__":
    main()
