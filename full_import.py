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
# FETCH 70 TENDERA (JEDINA IZMENA)
# =========================
def fetch_entity_ids():
    ids = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto("https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora")
        page.wait_for_load_state("networkidle")

        collected_pages = 0

        while len(ids) < 80 and collected_pages < 8:
            page.wait_for_selector("tr", timeout=15000)

            rows = page.locator("tr").all()

            for row in rows:
                text = row.inner_text()

                match = re.search(r"\b\d{6,}\b", text)
                if match:
                    ids.append(int(match.group()))

            ids = list(dict.fromkeys(ids))

            print(f"Collected: {len(ids)}")

            next_btn = page.locator("text=Sledeća").first

            if next_btn.is_visible():
                next_btn.click()
                page.wait_for_load_state("networkidle")
                collected_pages += 1
            else:
                break

        browser.close()

    print("AUTO IDS:", ids[:70])
    return ids[:70]

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
# ANALIZA (TVOJA + SIGURAN UPGRADE)
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

def analyze(text):
    text = clean_text(text)
    t = text.lower()

    if is_cancelled(text):
        print("⛔ OBUSTAVLJEN")
        return None

    prices = extract_prices(text)
    if not prices:
        return None

    lowest = min(prices)
    accepted = max(prices)

    multiple_bidders = len(prices) > 1

    rejection_keywords = [
        "odbijena ponuda",
        "ponuda se odbija",
        "neprihvatljiva",
        "nije prihvatljiva",
        "nije moguće utvrditi",
        "ne ispunjava uslove"
    ]

    rejection_detected = any(k in t for k in rejection_keywords)

    suspicious_price = accepted > lowest

    red_flag = (
        (multiple_bidders and suspicious_price) or
        rejection_detected
    )

    return {
        "winner": find_winner(text),
        "accepted": accepted,
        "lowest": lowest,
        "difference": accepted - lowest,
        "suspicious": suspicious_price,
        "multiple_bidders": multiple_bidders,
        "rejection_detected": rejection_detected,
        "red_flag": red_flag,
        "priority": "HIGH" if red_flag else "NORMAL"
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
