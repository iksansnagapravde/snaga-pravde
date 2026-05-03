import os
import re
import json
import sqlite3
import xml.etree.ElementTree as ET

from playwright.sync_api import sync_playwright
from docx import Document
from pdf2image import convert_from_path
import pytesseract

import openai

openai.api_key = os.getenv("OPENAI_API_KEY")

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

    return list(dict.fromkeys(ids))[:20]

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
    except Exception as e:
        print("OCR ERROR:", e)
    return text

def read_xml(path):
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        return ET.tostring(root, encoding="unicode")
    except:
        return ""

# =========================
# AI ANALYSIS (GOTOVO)
# =========================
def analyze_with_ai(text):
    try:
        prompt = f"""
Analiziraj dokument javne nabavke iz Srbije.

Vrati JSON niz svih ponuđača.

Za svakog ponuđača vrati:
- firma
- cena_bez_pdv
- cena_sa_pdv
- status (validna ili odbijena)

Posebno označi:
- pobednika ("pobednik": true)
- ako postoji samo jedan ponuđač ("jedan_ponudjac": true)

Vrati ISKLJUČIVO JSON niz.

TEKST:
{text[:12000]}
"""

        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )

        content = response["choices"][0]["message"]["content"]

        return json.loads(content)

    except Exception as e:
        print("AI ERROR:", e)
        return None

# =========================
# DETECTION
# =========================
def detect_anomalies(data):
    results = []

    if not data:
        return results

    # jedan ponudjac
    if any(p.get("jedan_ponudjac") for p in data):
        for p in data:
            p["flag"] = "jedan_ponudjac"
            results.append(p)
        return results

    valid = [p for p in data if p.get("status") == "validna"]

    if len(valid) < 2:
        return results

    winner = next((p for p in valid if p.get("pobednik")), None)
    lowest = min(valid, key=lambda x: x.get("cena_bez_pdv", 0))

    if winner and winner != lowest:
        results.append({
            "winner": winner,
            "lowest": lowest,
            "difference": winner["cena_bez_pdv"] - lowest["cena_bez_pdv"],
            "flag": "skuplji_pobedio"
        })

    return results

# =========================
# MAIN
# =========================
def main():
    final_results = []

    for eid in fetch_entity_ids():
        print("PROCESS:", eid)

        if already_processed(eid):
            continue

        path, ext = download_document(eid)
        if not path:
            continue

        if ext == "xml":
            text = read_xml(path)
        elif ext == "docx":
            text = read_docx(path)
        elif ext == "pdf":
            text = read_pdf(path)
        else:
            continue

        if not text:
            continue

        ai_data = analyze_with_ai(text)

        if not ai_data:
            print("AI FAIL")
            continue

        anomalies = detect_anomalies(ai_data)

        for a in anomalies:
            a["id"] = eid
            final_results.append(a)

        mark_processed(eid)

    with open("tenders.json", "w", encoding="utf-8") as f:
        json.dump(final_results, f, indent=2, ensure_ascii=False)

    print("DONE")

if __name__ == "__main__":
    main()
