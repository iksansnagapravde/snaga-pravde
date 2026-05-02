import os
import re
import json
import sqlite3
import xml.etree.ElementTree as ET

from playwright.sync_api import sync_playwright
from docx import Document
from pdf2image import convert_from_path
import pytesseract

BASE_URL = "https://jnportal.ujn.gov.rs"
os.makedirs("documents", exist_ok=True)

# =========================
# DATABASE
# =========================
conn = sqlite3.connect("contracts.db")
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS processed (
    entity_id INTEGER PRIMARY KEY
)
""")
conn.commit()

# =========================
# PROCESSED
# =========================
def already_processed(eid):
    c.execute("SELECT 1 FROM processed WHERE entity_id=?", (eid,))
    return c.fetchone() is not None

def mark_processed(eid):
    c.execute("INSERT OR IGNORE INTO processed VALUES (?)", (eid,))
    conn.commit()

# =========================
# FETCH IDS
# =========================
def fetch_entity_ids():
    ids = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto("https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora")
        page.wait_for_timeout(3000)

        rows = page.locator("tr").all()

        for row in rows:
            text = row.inner_text()
            match = re.search(r"\b\d{6,}\b", text)
            if match:
                ids.append(int(match.group()))

        browser.close()

    ids = list(dict.fromkeys(ids))
    print("AUTO IDS:", ids[:10])
    return ids[:10]

# =========================
# DOWNLOAD
# =========================
def download_document(eid):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()

            page.goto("https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora")
            page.wait_for_timeout(3000)

            rows = page.locator("tr").all()

            for row in rows:
                if str(eid) in row.inner_text():

                    button = row.locator("a, button").first

                    with page.expect_download(timeout=15000) as download_info:
                        button.click()

                    download = download_info.value
                    path = f"documents/{eid}_{download.suggested_filename}"
                    download.save_as(path)

                    print("DOWNLOADED:", path)

                    browser.close()

                    with open(path, "rb") as f:
                        head = f.read(200)

                    if head.startswith(b"%PDF"):
                        return path, "pdf"
                    elif b"<?xml" in head:
                        return path, "xml"
                    elif path.endswith(".docx"):
                        return path, "docx"
                    else:
                        return path, "unknown"

            print("❌ ID NIJE NAĐEN:", eid)
            browser.close()
            return None, None

    except Exception as e:
        print("DOWNLOAD ERROR:", e)
        return None, None

# =========================
# READERS
# =========================
def read_docx(path):
    try:
        doc = Document(path)
        return "\n".join([p.text for p in doc.paragraphs])
    except:
        return ""

def read_pdf(path):
    text = ""
    try:
        images = convert_from_path(path, dpi=400)
        for img in images:
            text += pytesseract.image_to_string(img, lang="srp+eng") + "\n"
    except Exception as e:
        print("OCR ERROR:", e)
    return text

# =========================
# HELPERS
# =========================
def clean_text(text):
    return re.sub(r"\s+", " ", text)

def is_cancelled(text):
    t = text.lower()
    return any(k in t for k in [
        "obustavi postupak",
        "postupak se obustavlja",
        "odluka o obustavi"
    ])

def extract_reason(text):
    t = text.lower()

    if "nije dostavljena nijedna ponuda" in t:
        return "nije bilo ponuda"
    if "sve ponude neprihvatljive" in t:
        return "sve ponude neprihvatljive"
    if "neprihvatljiva" in t:
        return "neprihvatljiva ponuda"

    return "nepoznato"

def extract_prices(text):
    prices = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", text)
    return sorted(set(float(p.replace(".", "").replace(",", ".")) for p in prices))

def extract_companies(text):
    companies = re.findall(r"[A-ZČĆŽŠĐ][A-ZČĆŽŠĐ\s]+DOO", text)
    return list(set(c.strip() for c in companies))

def extract_company_price_map(text):
    lines = text.split("\n")
    mapping = {}
    current_company = None

    for line in lines:
        company_match = re.search(r"[A-ZČĆŽŠĐ][A-ZČĆŽŠĐ\s]+DOO", line)
        if company_match:
            current_company = company_match.group().strip()

        price_match = re.search(r"\d{1,3}(?:\.\d{3})*,\d{2}", line)

        if current_company and price_match:
            price = float(price_match.group().replace(".", "").replace(",", "."))
            mapping[current_company] = price

    return mapping

# =========================
# ANALYZE
# =========================
def analyze(text):
    text = clean_text(text)

    # 🔴 OBUSTAVLJENI (NOVO)
    if is_cancelled(text):
        return {
            "status": "OBUSTAVLJEN",
            "reason": extract_reason(text),
            "companies": extract_companies(text),
            "priority": "HIGH"
        }

    prices = extract_prices(text)
    if not prices:
        return None

    companies = extract_companies(text)
    company_prices = extract_company_price_map(text)

    if not company_prices and companies:
        for i, c in enumerate(companies):
            if i < len(prices):
                company_prices[c] = prices[i]

    if not company_prices:
        return None

    sorted_bidders = sorted(company_prices.items(), key=lambda x: x[1])

    lowest_company, lowest_price = sorted_bidders[0]
    winner_company, winner_price = sorted_bidders[-1]

    losers = sorted_bidders[:-1]

    multiple_bidders = len(sorted_bidders) > 1

    suspicious_price = winner_price > lowest_price

    if not (suspicious_price or not multiple_bidders):
        return None

    return {
        "status": "DODELJEN",

        "winner": winner_company,
        "accepted_value": winner_price,
        "lowest_value": lowest_price,
        "difference": winner_price - lowest_price,

        "multiple_bidders": multiple_bidders,

        "losers": [
            {"company": c, "price": p}
            for c, p in losers
        ],

        "all_bidders": [
            {"company": c, "price": p}
            for c, p in sorted_bidders
        ],

        "red_flag": suspicious_price,
        "priority": "HIGH" if suspicious_price else "MEDIUM"
    }

# =========================
# MAIN
# =========================
def main():
    results = []

    for eid in fetch_entity_ids():
        print("\nPROCESS:", eid)

        path, ext = download_document(eid)

        if not path:
            continue

        if ext == "docx":
            text = read_docx(path)
        elif ext == "pdf":
            text = read_pdf(path)
        elif ext == "xml":
            text = open(path, encoding="utf-8", errors="ignore").read()
        else:
            continue

        data = analyze(text)

        if data:
            data["id"] = eid
            results.append(data)

        mark_processed(eid)

    with open("tenders.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("DONE")

if __name__ == "__main__":
    main()
