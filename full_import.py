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

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

# NOVO:
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


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
        pdf_url TEXT,
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
        pdf_url TEXT UNIQUE,
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
# PDF READER
# =====================================================

def extract_pdf_text_from_url(pdf_url):
    try:
        r = requests.get(pdf_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()

        reader = PdfReader(BytesIO(r.content))
        pages = []

        for page in reader.pages:
            txt = page.extract_text()
            if txt:
                pages.append(txt)

        return "\n".join(pages)

    except Exception as e:
        print("PDF ERROR:", e)
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
        r"Вредност уговора \(без ПДВ\)[:\s]+([\d\.\,]+)",
        r"Vrednost ugovora bez PDV[:\s]+([\d\.\,]+)",
        r"Vrednost ugovora \(bez PDV\)[:\s]+([\d\.\,]+)",
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

    section_match = re.search(
        r"Аналитички приказ поднетих понуда(.*?)(Стручна оцена|Уговор неће бити додељен|Образложење избора)",
        text,
        re.DOTALL | re.IGNORECASE
    )

    if not section_match:
        section_match = re.search(
            r"Analitički prikaz podnetih ponuda(.*?)(Stručna ocena|Ugovor neće biti dodeljen|Obrazloženje izbora)",
            text,
            re.DOTALL | re.IGNORECASE
        )

    if not section_match:
        return []

    section = section_match.group(1)

    found = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", section)

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

    # Čekaj da tabela zaista bude učitana
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.TAG_NAME, "table"))
    )

    time.sleep(5)

    soup = BeautifulSoup(driver.page_source, "html.parser")
    rows = soup.find_all("tr")

    parsed = []

    for row in rows:
        row_text = " ".join(row.get_text(" ", strip=True).split())

        if not row_text:
            continue

        if "Наручилац" in row_text and "Изабрани" in row_text:
            continue

        links = row.find_all("a", href=True)

        pdf_url = ""
        detail_url = ""

        for a in links:
            href = a["href"]
            full = urljoin(BASE_URL, href)

            if ".pdf" in href.lower():
                pdf_url = full

            if "/contract-eo/" in href:
                detail_url = full

        if pdf_url:
            parsed.append({
                "row_text": row_text,
                "pdf_url": pdf_url,
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
        bidder_count,pdf_url,detail_url,raw_text
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
        record["pdf_url"],
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
        pdf_url,detail_url
    ) VALUES (?,?,?,?,?,?,?,?)
    """, (
        record["supplier"],
        record["title"],
        record["publish_date"],
        record["accepted_bid"],
        record["median_bid"],
        budget_loss,
        record["pdf_url"],
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

    c.execute("""
    SELECT supplier,title,publish_date,
           accepted_bid,median_bid,budget_loss
    FROM suspicious_winners
    ORDER BY budget_loss DESC
    """)
    suspicious_rows = c.fetchall()

    conn.close()

    total_loss_vs_lowest = total_accepted - total_lowest
    total_loss_vs_median = total_accepted - total_median

    stats = {
        "broj_tendera": total_tenders,
        "ukupna_vrednost": format_rsd(total_budget),
        "ukupna_vrednost_eur": format_eur(total_budget / EUR_RATE),
        "broj_ugovora": total_tenders,
        "ugovorena_vrednost": format_rsd(total_accepted),
        "ugovorena_vrednost_eur": format_eur(total_accepted / EUR_RATE)
    }

    loss_data = {
        "najbolja_ponuda": round(total_lowest),
        "najbolja_ponuda_eur": round(total_lowest / EUR_RATE),

        "medijana_ponuda": round(total_median),
        "medijana_ponuda_eur": round(total_median / EUR_RATE),

        "prihvacena_ponuda": round(total_accepted),
        "prihvacena_ponuda_eur": round(total_accepted / EUR_RATE),

        "broj_analiziranih": total_tenders,

        "gubitak_prema_najboljoj": round(total_loss_vs_lowest),
        "gubitak_prema_najboljoj_eur": round(total_loss_vs_lowest / EUR_RATE),

        "gubitak_prema_medijani": round(total_loss_vs_median),
        "gubitak_prema_medijani_eur": round(total_loss_vs_median / EUR_RATE),

        "valuta_kurs_eur": EUR_RATE,
        "period_od": "2026-01-01"
    }

    suspicious_json = []

    for s in suspicious_rows:
        suspicious_json.append({
            "supplier": s[0],
            "title": s[1],
            "publish_date": s[2],
            "accepted_bid": round(s[3]),
            "median_bid": round(s[4]),
            "budget_loss": round(s[5])
        })

    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    with open(LOSS_FILE, "w", encoding="utf-8") as f:
        json.dump(loss_data, f, ensure_ascii=False, indent=2)

    with open(SUSPICIOUS_FILE, "w", encoding="utf-8") as f:
        json.dump(suspicious_json, f, ensure_ascii=False, indent=2)


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
        pdf_url = row["pdf_url"]
        detail_url = row["detail_url"]

        tender_key = row_text[:150] + "|" + pdf_url

        if tender_exists(tender_key):
            print("SKIP:", row_text[:60])
            continue

        pdf_text = extract_pdf_text_from_url(pdf_url)

        if not pdf_text:
            print("EMPTY PDF:", pdf_url)
            continue

        supplier = parse_supplier(pdf_text)
        budget_value = parse_budget_value(pdf_text)
        accepted_bid = parse_accepted_bid(pdf_text)

        prices = parse_bid_prices(pdf_text)

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
            "pdf_url": pdf_url,
            "detail_url": detail_url,
            "raw_text": pdf_text[:20000]
        }

        save_tender(record)

        if accepted_bid > median_bid and median_bid > 0:
            save_suspicious_winner(record)

        print(
            "SAVED:",
            supplier,
            "| lowest:", lowest_bid,
            "| median:", median_bid,
            "| accepted:", accepted_bid
        )

    driver.quit()
    write_outputs()

    print("=" * 60)
    print("DONE")
    print("stats.json updated")
    print("loss-data.json updated")
    print("suspicious-winners.json updated")
    print("=" * 60)


if __name__ == "__main__":
    main()
