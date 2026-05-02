import os
import re
import json
import sqlite3

from playwright.sync_api import sync_playwright
from docx import Document
from pdf2image import convert_from_path
import pytesseract
import pdfplumber

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

                    if path.endswith(".pdf"):
                        return path, "pdf"
                    elif path.endswith(".docx"):
                        return path, "docx"
                    else:
                        return path, "unknown"

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

    # pokušaj normalno čitanje
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"
    except:
        pass

    # fallback OCR
    if len(text.strip()) < 100:
        print("⚠ OCR fallback:", path)
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

def extract_prices(text):
    return sorted(set(
        float(p.replace(".", "").replace(",", "."))
        for p in re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", text)
    ))

# 🔥 SAMO PRAVE FIRME
def extract_companies(text):
    matches = re.findall(r"[A-ZČĆŽŠĐ][A-ZČĆŽŠĐ\s]{3,}(DOO|D\.O\.O|AD|PR)", text)
    return list(set(matches))

# 🔥 PAROVI FIRMA + CENA
def extract_pairs(text):
    lines = text.split("\n")
    pairs = []

    for line in lines:
        price_match = re.search(r"\d{1,3}(?:\.\d{3})*,\d{2}", line)
        if not price_match:
            continue

        price = float(price_match.group().replace(".", "").replace(",", "."))
        company = line.split(price_match.group())[0].strip()
        l = company.lower()

        if any(x in l for x in ["vrednost", "pdv", "ukupno", "procenjena"]):
            continue

        if re.search(r"(doo|d\.o\.o| ad | pr )", l):
            pairs.append({"company": company, "price": price})

    return pairs

# =========================
# ANALYZE
# =========================
def analyze(text):
    text = clean_text(text)

    pairs = extract_pairs(text)
    prices = extract_prices(text)

    if not pairs:
        return None

    lowest = min(pairs, key=lambda x: x["price"])
    highest = max(pairs, key=lambda x: x["price"])

    status = "OK"

    if highest["price"] > lowest["price"]:
        status = "SUMNJIVO"

    return {
        "winner": lowest["company"],
        "lowest_company": lowest["company"],
        "lowest_price": lowest["price"],
        "accepted": highest["price"],
        "difference": highest["price"] - lowest["price"],
        "all_bids": pairs,
        "status": status,
        "suspicious": status == "SUMNJIVO"
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
