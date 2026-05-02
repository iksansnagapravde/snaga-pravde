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
# AUTO FETCH IDS
# =========================
def fetch_entity_ids():
    ids = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto("https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora")
        page.wait_for_load_state("networkidle")

        page.wait_for_selector("tr", timeout=15000)

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
            page.wait_for_load_state("networkidle")

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
        images = convert_from_path(path, dpi=300)
        for img in images:
            text += pytesseract.image_to_string(img)
    except:
        pass
    return text

# =========================
# ANALIZA
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

def extract_prices(text):
    prices = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", text)
    return sorted(set(float(p.replace(".", "").replace(",", ".")) for p in prices))

def find_winner(text):
    parts = text.split(".")
    for i, part in enumerate(parts):
        if "dodeljuje" in part.lower():
            for j in range(i, i+3):
                if j < len(parts):
                    if "doo" in parts[j].lower():
                        return parts[j].strip()
    return None

# =========================
# NOVO: DETEKCIJA ODBIJENIH
# =========================

REJECTION_PATTERNS = [
    "odbijena ponuda",
    "ponuda se odbija",
    "neprihvatljiva",
    "nije prihvatljiva",
    "nije dostavio",
    "ne ispunjava uslove",
    "odbijaju se ponude"
]

def detect_rejections(text):
    t = text.lower()
    return [p for p in REJECTION_PATTERNS if p in t]

# =========================
# NOVO: DETEKCIJA FIRMI
# =========================

def extract_companies(text):
    companies = re.findall(r"[A-ZČĆŠĐŽ][A-ZČĆŠĐŽa-z0-9\s\.\-]{3,}(?:doo|d\.o\.o\.)", text, re.IGNORECASE)
    return list(set(companies))

# =========================
# GLAVNA ANALIZA (UNAPREĐENA)
# =========================

def analyze(text):
    text = clean_text(text)

    if is_cancelled(text):
        print("⛔ OBUSTAVLJEN")
        return None

    prices = extract_prices(text)
    if not prices:
        return None

    lowest = min(prices)
    highest = max(prices)

    winner = find_winner(text)

    # NOVO
    rejections = detect_rejections(text)
    companies = extract_companies(text)

    multiple_bidders = len(prices) > 1 or len(companies) > 1
    suspicious_price = highest > lowest

    return {
        "winner": winner,
        "accepted": highest,
        "lowest": lowest,
        "difference": highest - lowest,
        "suspicious": suspicious_price,

        # 🔥 NOVO
        "prices": prices,
        "companies": companies,
        "num_prices": len(prices),
        "num_companies": len(companies),

        "multiple_bidders": multiple_bidders,

        "rejections_detected": len(rejections) > 0,
        "rejection_keywords": rejections,

        # 🔥 GLAVNI SIGNAL
        "red_flag": multiple_bidders and suspicious_price,

        # PRIORITET
        "priority": (
            "HIGH"
            if (len(rejections) > 0 or suspicious_price)
            else "NORMAL"
        )
    }

# =========================
# MAIN
# =========================
def main():
    results = []

    for eid in fetch_entity_ids():
        print("\nPROCESS:", eid)

        if already_processed(eid):
            print("SKIP (already)")
            continue

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
