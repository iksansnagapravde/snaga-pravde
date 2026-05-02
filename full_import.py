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

        page.goto(BASE_URL + "/odluke-o-dodeli-ugovora")
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

            page.goto(BASE_URL + "/odluke-o-dodeli-ugovora")
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

def extract_prices(text):
    prices = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", text)
    return sorted(set(float(p.replace(".", "").replace(",", ".")) for p in prices))

# =========================
# 🔥 FINAL PARSER (OTPORNOST NA OCR)
# =========================
def extract_full_analysis(text):
    result = {
        "winner": None,
        "pairs": [],
        "lowest_company": None,
        "lowest_price": None
    }

    lines = text.split("\n")

    # 🔥 WINNER (otporniji)
    for i, line in enumerate(lines):
        l = line.lower()

        if "dodelj" in l and ("subjekt" in l or "ugovor" in l):
            for j in range(i+1, min(i+6, len(lines))):
                candidate = lines[j].strip()

                if any(x in candidate.lower() for x in ["doo", "d.o.o", "pr", "ad"]):
                    result["winner"] = candidate
                    break
            break

    # 🔥 TABELA (otpornija)
    in_table = False

    for line in lines:
        l = line.lower()

        if "analiti" in l:
            in_table = True
            continue

        if in_table:
            if "ocena" in l:
                break

            price_match = re.search(r"\d{1,3}(?:\.\d{3})*,\d{2}", line)

            if price_match:
                price = float(price_match.group().replace(".", "").replace(",", "."))
                company = line.split(price_match.group())[0].strip()

                if len(company) > 5:
                    result["pairs"].append({
                        "company": company,
                        "price": price
                    })

    if result["pairs"]:
        lowest = min(result["pairs"], key=lambda x: x["price"])
        result["lowest_company"] = lowest["company"]
        result["lowest_price"] = lowest["price"]

    return result

# =========================
# ANALYZE
# =========================
def analyze(text):
    original_text = text
    text = clean_text(text)

    if is_cancelled(text):
        print("⛔ OBUSTAVLJEN")
        return None

    prices = extract_prices(text)
    if not prices:
        return None

    lowest = min(prices)
    accepted = max(prices)

    structured = extract_full_analysis(original_text)

    winner = structured["winner"] if structured["winner"] else "NEPOZNATO"
    lowest_company = structured["lowest_company"]
    lowest_price = structured["lowest_price"]

    suspicious = False

    if winner != "NEPOZNATO" and lowest_company:
        if winner.lower() not in lowest_company.lower():
            suspicious = True

    return {
        "winner": winner,
        "accepted": accepted,
        "lowest": lowest,
        "difference": accepted - lowest,
        "lowest_company": lowest_company,
        "lowest_price": lowest_price,
        "all_bids": structured["pairs"],
        "status": "SUMNJIVO" if suspicious else "OK",
        "suspicious": suspicious
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
            print("✅", data)
            data["id"] = eid
            results.append(data)

        mark_processed(eid)

    with open("tenders.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("DONE")

if __name__ == "__main__":
    main()
