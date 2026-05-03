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

# =========================
# 🔥 FIRMA + CENA + STATUS
# =========================
def extract_company_price_pairs(text):
    pairs = []
    lines = text.split("\n")

    for line in lines:
        if "doo" in line.lower():
            company_match = re.search(r"[A-ZČĆŽŠĐ][A-ZČĆŽŠĐ\s]+DOO", line)
            price_match = re.search(r"\d{1,3}(?:\.\d{3})*,\d{2}", line)

            if company_match and price_match:
                company = company_match.group().strip()
                price = float(price_match.group().replace(".", "").replace(",", "."))

                status = "validna"
                low = line.lower()

                if any(k in low for k in [
                    "odbij", "neprihvat", "ne ispunjava", "diskvalifik"
                ]):
                    status = "odbijena"

                pairs.append({
                    "firma": company,
                    "cena": price,
                    "status": status
                })

    return pairs

# =========================
# 🔥 WINNER DETEKCIJA
# =========================
def find_winner_company(text, companies):
    t = text.lower()

    for c in companies:
        if c.lower() in t:
            idx = t.find(c.lower())
            snippet = t[max(0, idx-100):idx+100]

            if any(k in snippet for k in [
                "dodeljuje", "izabrana", "ugovor se dodeljuje"
            ]):
                return c

    return None

# =========================
# ANALYZE
# =========================
def analyze(text):
    text = clean_text(text)

    if is_cancelled(text):
        print("⛔ OBUSTAVLJEN")
        return None

    pairs = extract_company_price_pairs(text)

    if not pairs:
        return None

    validne = [p for p in pairs if p["status"] == "validna"]

    if not validne:
        return None

    companies = [p["firma"] for p in pairs]
    winner_name = find_winner_company(text, companies)

    winner = None
    if winner_name:
        winner = next((p for p in pairs if p["firma"] == winner_name), None)

    if not winner:
        winner = max(validne, key=lambda x: x["cena"])

    lowest_valid = min(validne, key=lambda x: x["cena"])
    lowest_all = min(pairs, key=lambda x: x["cena"])

    broj_ponudjaca = len(pairs)

    flags = []

    if broj_ponudjaca == 1:
        flags.append("jedan_ponudjac")

    if winner["cena"] > lowest_valid["cena"]:
        flags.append("skuplji_pobedio")

    if lowest_all["status"] == "odbijena":
        flags.append("najjeftiniji_odbijen")

    if not flags:
        return None

    return {
        "winner": winner["firma"],
        "winner_price": winner["cena"],
        "lowest_valid": lowest_valid["firma"],
        "lowest_valid_price": lowest_valid["cena"],
        "difference": winner["cena"] - lowest_valid["cena"],
        "broj_ponudjaca": broj_ponudjaca,
        "ponude": pairs,
        "flags": flags,
        "suspicious": True
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
