import os
import re
import json
import sqlite3
import xml.etree.ElementTree as ET

from playwright.sync_api import sync_playwright
from docx import Document
from pdf2image import convert_from_path
import pytesseract

from openai import OpenAI

# =========================
# CONFIG
# =========================
client = OpenAI(api_key="YOUR_API_KEY")

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
    return ids[:20]  # povećano na 20

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
            text += pytesseract.image_to_string(img)
    except:
        pass
    return text

def read_xml(path):
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        return ET.tostring(root, encoding="unicode")
    except:
        return ""

# =========================
# AI ANALYZA
# =========================
def analyze_with_ai(text):
    try:
        prompt = f"""
Izvuci podatke o javnoj nabavci.

Vrati JSON:

{{
  "ponude": [
    {{
      "firma": "",
      "cena": 0,
      "status": "validna ili odbijena"
    }}
  ],
  "pobednik": ""
}}

TEKST:
{text[:12000]}
"""

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )

        return json.loads(response.choices[0].message.content)

    except Exception as e:
        print("AI ERROR:", e)
        return None

# =========================
# DETEKCIJA
# =========================
def detect_anomalies(data):
    ponude = data.get("ponude", [])
    pobednik = data.get("pobednik")

    if not ponude:
        return None

    validne = [p for p in ponude if p["status"] == "validna"]
    if not validne:
        return None

    winner = next((p for p in ponude if p["firma"] == pobednik), None)
    if not winner:
        winner = max(validne, key=lambda x: x["cena"])

    lowest = min(validne, key=lambda x: x["cena"])

    flags = []

    if len(ponude) == 1:
        flags.append("jedan_ponudjac")

    if winner["cena"] > lowest["cena"]:
        flags.append("skuplji_pobedio")

    if not flags:
        return None

    return {
        "winner": winner,
        "lowest": lowest,
        "difference": winner["cena"] - lowest["cena"],
        "flags": flags,
        "ponude": ponude
    }

# =========================
# MAIN
# =========================
def main():
    results = []

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
            continue

        result = detect_anomalies(ai_data)
        if result:
            result["id"] = eid
            results.append(result)

        mark_processed(eid)

    with open("tenders.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("DONE")

if __name__ == "__main__":
    main()
