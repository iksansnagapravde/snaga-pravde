import os
import re
import json
import sqlite3

from playwright.sync_api import sync_playwright
from docx import Document
from pdf2image import convert_from_path
import pytesseract

# 🔥 NOVO – PDF TEXT (BEZ OCR)
from pdfminer.high_level import extract_text

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

                    with open(path, "rb") as f:
                        head = f.read(200)

                    if head.startswith(b"%PDF"):
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

# 🔥 KLJUČNO – PRVO PDF TEXT, PA OCR
def read_pdf(path):
    try:
        text = extract_text(path)
        if text and len(text) > 100:
            return text
    except:
        pass

    print("⚠ OCR fallback")

    text = ""
    try:
        images = convert_from_path(path, dpi=300)
        for img in images:
            text += pytesseract.image_to_string(img, lang="srp")
    except:
        pass

    return text

# =========================
# ANALIZA
# =========================
def clean_text(text):
    return re.sub(r"\s+", " ", text)

def extract_prices(text):
    prices = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", text)
    return sorted(set(float(p.replace(".", "").replace(",", ".")) for p in prices))

# 🔥 FINAL – PRECIZAN WINNER
def extract_winner(text):
    # traži deo gde se dodeljuje ugovor
    match = re.search(
        r"dodeljuje\s+se\s+ugovor.*?\n(.*)",
        text,
        re.IGNORECASE
    )
    if match:
        return match.group(1).strip()

    # fallback – firma sa DOO/PR
    lines = text.split("\n")
    for line in lines:
        if any(x in line.lower() for x in ["doo", "d.o.o", "pr", "ad"]):
            return line.strip()

    return None

def analyze(text):
    prices = extract_prices(text)
    if not prices:
        return None

    lowest = min(prices)
    accepted = max(prices)

    winner = extract_winner(text)

    return {
        "winner": winner,
        "accepted": accepted,
        "lowest": lowest,
        "difference": accepted - lowest,
        "suspicious": accepted > lowest
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

        print("\n--- TEKST ---")
        print(text[:500])

        data = analyze(text)

        if data:
            print("✅ DETEKTOVANO:", data)
            data["id"] = eid
            results.append(data)
        else:
            print("❌ NIŠTA")

        mark_processed(eid)

    with open("tenders.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("DONE")

if __name__ == "__main__":
    main()
