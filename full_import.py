import json
import re
import sqlite3
import statistics
import time
from datetime import datetime
from io import BytesIO

import requests
from docx import Document

from selenium import webdriver
from selenium.webdriver.chrome.options import Options


# =====================================================
# CONFIG
# =====================================================

BASE_URL = "https://jnportal.ujn.gov.rs"
DB_FILE = "contracts.db"
STATS_FILE = "stats.json"
LOSS_FILE = "loss-data.json"

EUR_RATE = 117.2
REQUEST_TIMEOUT = 60


# =====================================================
# DB
# =====================================================

def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
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


def tender_exists(tender_key):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT 1 FROM tenders WHERE tender_key=?", (tender_key,))
    exists = c.fetchone() is not None
    conn.close()
    return exists


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
# DOCX
# =====================================================

def extract_docx_text_from_bytes(content):
    try:
        doc = Document(BytesIO(content))
        return "\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())
    except Exception as e:
        print("DOCX ERROR:", e)
        return ""


# =====================================================
# PARSERS
# =====================================================

def parse_supplier(text):
    m = re.search(r"(додељује|dodeljuje).*?:\s*(.+?)(?:\n|PIB)", text, re.I)
    if m:
        return re.sub(r"\s+", " ", m.group(2)).strip()
    return "UNKNOWN"


def parse_budget_value(text):
    m = re.search(r"(процењена|procenjena).*?:\s*([\d\.,]+)", text, re.I)
    return money_to_float(m.group(2)) if m else 0


def parse_accepted_bid(text):
    m = re.search(r"(вредност|vrednost).*?:\s*([\d\.,]+)", text, re.I)
    return money_to_float(m.group(2)) if m else 0


def parse_bid_prices(text):
    prices = []
    for val in re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", text):
        num = money_to_float(val)
        if 1000 < num < 1_000_000_000:
            prices.append(num)
    return sorted(prices)


# =====================================================
# SELENIUM
# =====================================================

def fetch_doc_links():
    print("FETCHING LINKS...")

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")

    driver = webdriver.Chrome(options=options)
    driver.get("https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora")

    time.sleep(6)

    links = driver.find_elements("tag name", "a")

    doc_links = []
    for a in links:
        href = a.get_attribute("href")
        if href and "GetDocuments.ashx" in href:
            doc_links.append(href)

    driver.quit()

    print("FOUND:", len(doc_links))
    return doc_links


# =====================================================
# NEW: EXTRACT REAL DOCX LINK
# =====================================================

def extract_real_docx_link(html):
    matches = re.findall(r'href="([^"]+\.docx[^"]*)"', html, re.I)
    if matches:
        return BASE_URL + matches[0]
    return None


# =====================================================
# DOWNLOAD (FIXED FINAL)
# =====================================================

def download_docx(url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0"
        }

        print("OPENING PAGE:", url)

        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)

        if r.status_code != 200:
            return None

        html = r.text

        real_docx = extract_real_docx_link(html)

        if not real_docx:
            print("NO DOCX LINK FOUND")
            return None

        print("REAL DOCX:", real_docx)

        file = requests.get(real_docx, headers=headers, timeout=REQUEST_TIMEOUT)

        print("DOCX STATUS:", file.status_code)

        if file.status_code == 200:
            return file.content

    except Exception as e:
        print("DOWNLOAD ERROR:", e)

    return None


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

    stats = {
        "broj_tendera": row[0],
        "ukupna_vrednost": format_rsd(row[1]),
        "ukupna_vrednost_eur": format_eur(row[1] / EUR_RATE),
        "broj_ugovora": row[0],
        "ugovorena_vrednost": format_rsd(row[2]),
        "ugovorena_vrednost_eur": format_eur(row[2] / EUR_RATE)
    }

    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def write_loss_data():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
        SELECT lowest_bid, median_bid, accepted_bid
        FROM tenders
        WHERE accepted_bid > 0 AND lowest_bid > 0 AND bidder_count >= 2
    """)

    rows = c.fetchall()
    conn.close()

    if not rows:
        print("NO VALID DATA FOR LOSS")
        return

    loss_low = 0
    loss_med = 0

    for low, med, acc in rows:
        if acc > low:
            loss_low += acc - low
        if acc > med:
            loss_med += acc - med

    result = {
        "najbolja_ponuda": sum(r[0] for r in rows),
        "medijana_ponuda": sum(r[1] for r in rows),
        "prihvacena_ponuda": sum(r[2] for r in rows),
        "broj_analiziranih": len(rows),
        "gubitak_prema_najboljoj": loss_low,
        "gubitak_prema_medijani": loss_med,
        "valuta_kurs_eur": EUR_RATE,
        "period_od": "2026-01-01"
    }

    with open(LOSS_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


# =====================================================
# MAIN
# =====================================================

def main():
    init_db()

    doc_links = fetch_doc_links()

    for idx, doc_url in enumerate(doc_links):
        try:
            print("=" * 40)
            print("PROCESSING:", doc_url)

            if tender_exists(doc_url):
                print("SKIPPED (EXISTS)")
                continue

            content = download_docx(doc_url)
            if not content:
                print("NO CONTENT")
                continue

            text = extract_docx_text_from_bytes(content)

            if not text:
                print("EMPTY TEXT")
                continue

            prices = parse_bid_prices(text)
            supplier = parse_supplier(text)

            record = {
                "tender_key": doc_url,
                "title": f"Tender {idx}",
                "supplier": supplier,
                "publish_date": datetime.today().strftime("%d.%m.%Y"),
                "budget_value": parse_budget_value(text),
                "lowest_bid": min(prices) if prices else 0,
                "median_bid": statistics.median(prices) if prices else 0,
                "accepted_bid": parse_accepted_bid(text),
                "bidder_count": len(prices),
                "raw_text": text[:20000]
            }

            save_tender(record)

            print("SAVED:", supplier)

            time.sleep(1)

        except Exception as e:
            print("ERROR:", e)

    write_outputs()
    write_loss_data()

    print("DONE")


if __name__ == "__main__":
    main()
