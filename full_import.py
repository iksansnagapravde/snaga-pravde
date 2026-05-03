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
# ANALIZA – HELPERS
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
# 🔥 NOVO – FIRME
# =========================
def extract_companies(text):
    return list(set(re.findall(r"[A-ZČĆŽŠĐ][A-ZČĆŽŠĐ\s]+DOO", text)))

# =========================
# 🔥 NOVO – KONKURENCIJA
# =========================
def detect_competition(text):
    lines = text.split("\n")

    companies = []
    prices = []

    for line in lines:
        if "doo" in line.lower():
            companies.append(line.strip())

        match = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", line)
        if match:
            for m in match:
                prices.append(float(m.replace(".", "").replace(",", ".")))

    companies = list(dict.fromkeys(companies))
    prices = sorted(list(set(prices)))

    return {
        "companies": companies,
        "prices": prices
    }

# =========================
# 🔥 NOVO – RAZLOZI ODBIJANJA
# =========================
def detect_rejection_reasons(text):
    patterns = [
        "neprihvatljiva ponuda",
        "ponuda se odbija",
        "nije prihvatljiva",
        "ne ispunjava uslove",
        "diskvalifikovan",
        "nije dostavio",
        "ne odgovara"
    ]

    found = []
    t = text.lower()

    for p in patterns:
        if p in t:
            found.append(p)

    return found

# =========================
# ANALYZE (GLAVNA LOGIKA)
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
    accepted = max(prices)

    competition = detect_competition(text)
    companies = extract_companies(text)
    reasons = detect_rejection_reasons(text)

    broj_ponudjaca = len(competition["companies"]) if competition else 0

    status = "nepoznato"

    if broj_ponudjaca == 1:
        status = "jedan_ponudjac"

    elif broj_ponudjaca > 1:
        if accepted == lowest:
            status = "najjeftiniji_pobedio"
        elif accepted > lowest:
            if reasons:
                status = "skuplji_pobedio_ali_objasnjeno"
            else:
                status = "SUMNJIVO"

    return {
        "winner": find_winner(text),

        "accepted": accepted,
        "lowest": lowest,
        "difference": accepted - lowest,

        "companies": companies,
        "competition": competition,

        "broj_ponudjaca": broj_ponudjaca,
        "razlozi_odbijanja": reasons,

        "status": status,
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
