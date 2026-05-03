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
            text += pytesseract.image_to_string(img, lang="srp+eng")
    except:
        pass
    return text

# =========================
# ANALYZE HELPERS
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
    matches = re.findall(r"\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})?", text)

    prices = []
    for m in matches:
        clean = m.replace(" ", "").replace(".", "").replace(",", ".")
        try:
            val = float(clean)
            if 1000 < val < 1000000000:
                prices.append(val)
        except:
            pass

    return sorted(set(prices))

def find_winner(text):
    lines = text.split("\n")

    # 1. traži ključne fraze + firmu blizu
    for i, line in enumerate(lines):
        l = line.lower()

        if any(k in l for k in [
            "dodeljuje",
            "dodela ugovora",
            "izabrana ponuda",
            "ponuđač kome se",
            "ugovor se dodeljuje"
        ]):
            for j in range(i, min(i+6, len(lines))):
                if "doo" in lines[j].lower():
                    return lines[j].strip()

    # 2. fallback – uzmi prvu firmu u dokumentu
    for line in lines:
        if "doo" in line.lower():
            return line.strip()

    return "Nepoznato"


def detect_rejection_reasons(text):
    t = text.lower()

    patterns = [
        "neprihvatljiva ponuda",
        "ponuda se odbija",
        "nije prihvatljiva",
        "ne ispunjava uslove",
        "diskvalifikovan",
        "nije dostavio",
        "ne odgovara",
        "odbijena ponuda",
        "nevažeća ponuda"
    ]

    found = []

    for p in patterns:
        if p in t:
            found.append(p)

    return found

# =========================
# ANALYZE
# =========================
def analyze(text):
    text = clean_text(text)

    if is_cancelled(text):
        return None

    prices = extract_prices(text)
    if len(prices) < 2:
        return None

    lowest = min(prices)
    accepted = max(prices)
    reasons = detect_rejection_reasons(text)

    status = "ok"

    if accepted > lowest:
        status = "SUMNJIVO" if not reasons else "objasnjeno"

    return {
        "winner": find_winner(text),
        "accepted": accepted,
        "lowest": lowest,
        "difference": accepted - lowest,
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

    # =========================
    # STATS + LOSS
    # =========================
    total_value = 0
    total_loss = 0
    count = 0
    prices = []

    for r in results:
        accepted = r["accepted"]
        lowest = r["lowest"]

        total_value += accepted
        total_loss += (accepted - lowest)
        prices.append(accepted)
        count += 1

    median = sorted(prices)[len(prices)//2] if prices else 0

    stats = {
        "broj_tendera": count,
        "ukupna_vrednost": f"{int(total_value)} RSD",
        "ukupna_vrednost_eur": f"{int(total_value/117.2)} EUR",
        "broj_ugovora": count,
        "ugovorena_vrednost": f"{int(total_value)} RSD",
        "ugovorena_vrednost_eur": f"{int(total_value/117.2)} EUR"
    }

    loss = {
        "najbolja_ponuda": f"{int(min(prices)) if prices else 0} RSD",
        "srednja_ponuda": f"{int(median)} RSD",
        "prihvacena_ponuda": f"{int(max(prices)) if prices else 0} RSD",
        "gubitak_prema_najboljoj": f"{int(total_loss)} RSD",
        "gubitak_prema_srednjoj": f"{int(total_loss - median)} RSD",
        "broj_analiziranih": count
    }

    with open("tenders.json", "w") as f:
        json.dump(results, f, indent=2)

    with open("stats.json", "w") as f:
        json.dump(stats, f, indent=2)

    with open("loss-data.json", "w") as f:
        json.dump(loss, f, indent=2)

    print("DONE")

if __name__ == "__main__":
    main()
