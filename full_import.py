import os
import re
import json
import statistics
import sqlite3
from datetime import datetime

from playwright.sync_api import sync_playwright
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
CREATE TABLE IF NOT EXISTS tenders (
    entity_id INTEGER PRIMARY KEY,
    lowest REAL,
    medijana REAL,
    accepted REAL,
    loss_low REAL,
    loss_medijana REAL,
    created_at TEXT
)
""")
conn.commit()

# =========================
# IDS
# =========================

def fetch_entity_ids():
    return [675152, 670413, 666041]

# =========================
# DETECT TYPE
# =========================

def detect_type(content):
    if content.startswith(b"%PDF"):
        return "pdf"
    if b"<?xml" in content[:200]:
        return "xml"
    if b"<html" in content[:500].lower():
        return "html"
    return "unknown"

# =========================
# DOWNLOAD (MULTI FORMAT)
# =========================

def download_document(eid):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.goto("https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora")

            # čekaj tabelu
            page.wait_for_timeout(5000)

            # nađi red sa ID-jem
            row = page.locator(f"text={eid}").first

            if not row:
                print("ROW NOT FOUND")
                browser.close()
                return None, None

            # klikni dugme za dokument (strelica)
            row.click()

            page.wait_for_timeout(2000)

            # sad tražimo linkove za download
            links = page.locator("a[href*='download'], a[href*='document']").all()

            for link in links:
                href = link.get_attribute("href")

                if not href:
                    continue

                if href.startswith("/"):
                    href = BASE_URL + href

                response = page.request.get(href)
                content = response.body()

                # DETEKCIJA
                if content.startswith(b"%PDF"):
                    doc_type = "pdf"
                elif b"<?xml" in content[:200]:
                    doc_type = "xml"
                elif b"<html" in content[:500].lower():
                    continue  # preskoči HTML
                else:
                    continue

                filename = f"documents/{eid}.{doc_type}"

                with open(filename, "wb") as f:
                    f.write(content)

                print(f"DOWNLOADED {doc_type.upper()}:", filename)

                browser.close()
                return filename, doc_type

            print("NO VALID DOCUMENT")
            browser.close()
            return None, None

    except Exception as e:
        print("DOWNLOAD ERROR:", e)
        return None, None
# =========================
# OCR (PDF)
# =========================

def extract_text_pdf(path):
    text = ""
    try:
        images = convert_from_path(path, dpi=300)
        print("IMAGES:", len(images))

        for img in images:
            t = pytesseract.image_to_string(
                img,
                lang="eng",
                config="--oem 3 --psm 6"
            )
            text += t + "\n"
    except Exception as e:
        print("OCR ERROR:", e)

    return text

# =========================
# CLEAN
# =========================

def clean_text(text):
    text = text.replace("\x0c", " ")
    text = re.sub(r"\s+", " ", text)
    return text

# =========================
# PRICES
# =========================

def extract_prices(text):
    prices = []
    matches = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", text)

    for m in matches:
        try:
            num = float(m.replace(".", "").replace(",", "."))
            if 10000 < num < 1_000_000_000:
                prices.append(num)
        except:
            continue

    prices = sorted(set(prices))
    print("FINAL PRICES:", prices)
    return prices

# =========================
# ACCEPTED
# =========================

def find_accepted(text):
    for part in text.split("."):
        if "вредност уговора" in part.lower():
            m = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", part)
            if m:
                return float(m[0].replace(".", "").replace(",", "."))
    return None

# =========================
# WINNER
# =========================

def find_winner(text):
    parts = text.split(".")
    for i, part in enumerate(parts):
        if "додељује" in part.lower():
            for j in range(i, i + 3):
                if j < len(parts):
                    if "доо" in parts[j].lower() or "a.d." in parts[j].lower():
                        return parts[j].strip()
    return None

# =========================
# ANALYZE
# =========================

def analyze(prices, accepted):
    if not prices:
        return None

    lowest = min(prices)
    med = statistics.median(prices)

    if not accepted:
        accepted = prices[-1]

    loss_low = max(0, accepted - lowest)
    loss_med = max(0, accepted - med)

    return lowest, med, accepted, loss_low, loss_med

# =========================
# MAIN
# =========================

def main():
    ids = fetch_entity_ids()
    output = []

    for eid in ids:
        print("\nPROCESS:", eid)

        path, doc_type = download_document(eid)
        if not path:
            continue

        # 👉 PARSING PO TIPU
        if doc_type == "pdf":
            text = extract_text_pdf(path)
        else:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()

        if len(text) < 50:
            print("TEXT TOO SHORT")
            continue

        text = clean_text(text)

        prices = extract_prices(text)
        accepted = find_accepted(text)
        winner = find_winner(text)

        result = analyze(prices, accepted)

        if result:
            lowest, med, accepted, loss_low, loss_med = result

            output.append({
                "id": eid,
                "winner": winner,
                "accepted": accepted,
                "lowest": lowest,
                "median": med,
                "loss_low": loss_low,
                "loss_median": loss_med
            })

    with open("tenders.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print("\nDONE")

if __name__ == "__main__":
    main()
