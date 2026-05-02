import os
import re
import json
import sqlite3

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

def mark_processed(eid):
    c.execute("INSERT OR IGNORE INTO processed VALUES (?)", (eid,))
    conn.commit()

# =========================
# FETCH
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

            page.goto(BASE_URL + "/odluke-o-dodeli-ugovora")
            page.wait_for_timeout(3000)

            rows = page.locator("tr").all()

            for row in rows:
                if str(eid) in row.inner_text():

                    button = row.locator("a, button").first

                    with page.expect_download(timeout=15000) as d:
                        button.click()

                    download = d.value
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
        return "\n".join(p.text for p in doc.paragraphs)
    except:
        return ""

def read_pdf(path):
    text = ""
    try:
        images = convert_from_path(path, dpi=400)
        for img in images:
            text += pytesseract.image_to_string(img, lang="srp+eng", config="--psm 6") + "\n"
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
    return "obustav" in t

def extract_reason(text):
    t = text.lower()
    if "nije dostavljena nijedna ponuda" in t:
        return "nije bilo ponuda"
    if "neprihvatljive" in t:
        return "sve ponude neprihvatljive"
    return "nepoznato"

def extract_prices(text):
    prices = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", text)
    return sorted(set(float(p.replace(".", "").replace(",", ".")) for p in prices))

def extract_companies(text):
    return list(set(re.findall(r"[A-ZČĆŽŠĐ][A-ZČĆŽŠĐ\s]+DOO", text)))

# 🔥 ROBUSTAN MAP
def extract_company_price_map(text):
    companies = extract_companies(text)
    prices = extract_prices(text)

    mapping = {}

    if not companies or not prices:
        return mapping

    # direktno mapiranje ako je moguće
    if len(companies) == len(prices):
        for i in range(len(companies)):
            mapping[companies[i]] = prices[i]
        return mapping

    # fallback
    for i, c in enumerate(companies):
        if i < len(prices):
            mapping[c] = prices[i]

    return mapping

# =========================
# ANALYZE
# =========================
def analyze(text):
    text = clean_text(text)

    # OBUSTAVLJEN
    if is_cancelled(text):
        return {
            "status": "OBUSTAVLJEN",
            "reason": extract_reason(text),
            "companies": extract_companies(text),
            "risk_score": 80
        }

    prices = extract_prices(text)
    companies = extract_companies(text)
    mapping = extract_company_price_map(text)

    if not mapping:
        return None

    bidders = sorted(mapping.items(), key=lambda x: x[1])

    lowest = bidders[0]
    winner = bidders[-1]

    multiple = len(bidders) > 1
    suspicious = winner[1] > lowest[1]

    if not (suspicious or not multiple):
        return None

    return {
        "status": "DODELJEN",
        "winner": winner[0],
        "accepted_value": winner[1],
        "lowest_value": lowest[1],
        "difference": winner[1] - lowest[1],
        "losers": bidders[:-1],
        "multiple_bidders": multiple,
        "risk_score": 90 if suspicious else 50
    }

# =========================
# LEVEL 4
# =========================
def generate_leads(results):
    leads = []

    for r in results:
        if r["status"] == "OBUSTAVLJEN":
            for c in r.get("companies", []):
                leads.append({"company": c, "reason": "obustavljen tender"})

        if r["status"] == "DODELJEN":
            for l in r.get("losers", []):
                leads.append({"company": l[0], "price": l[1]})

    return leads

def generate_stats(results):
    return {
        "total": len(results),
        "obustavljeni": sum(1 for r in results if r["status"] == "OBUSTAVLJEN"),
        "sumnjivi": sum(1 for r in results if r.get("risk_score", 0) > 70)
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
            data["id"] = eid
            results.append(data)
            print("✅ DETEKTOVANO:", data)
        else:
            print("❌ NIŠTA")

        mark_processed(eid)

    with open("tenders.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    leads = generate_leads(results)
    with open("leads.json", "w", encoding="utf-8") as f:
        json.dump(leads, f, indent=2, ensure_ascii=False)

    stats = generate_stats(results)
    with open("stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    print("DONE")

if __name__ == "__main__":
    main()
