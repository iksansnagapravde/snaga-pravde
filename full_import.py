import os
import re
import json
import sqlite3
import xml.etree.ElementTree as ET

from playwright.sync_api import sync_playwright
from docx import Document
from pdf2image import convert_from_path
import pytesseract

try:
    import pdfplumber
except ImportError:
    pdfplumber = None


BASE_URL = "https://jnportal.ujn.gov.rs"
DOCUMENTS_DIR = "documents"
DB_PATH = "contracts.db"
LIMIT_IDS = 10

os.makedirs(DOCUMENTS_DIR, exist_ok=True)


# =========================
# DATABASE
# =========================
conn = sqlite3.connect(DB_PATH)
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
def fetch_entity_ids(page):
    ids = []

    page.goto(BASE_URL + "/odluke-o-dodeli-ugovora")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("tr", timeout=15000)

    rows = page.locator("tr").all()

    for row in rows:
        try:
            text = row.inner_text()
        except:
            continue

        match = re.search(r"\b\d{6,}\b", text)
        if match:
            ids.append(int(match.group()))

    ids = list(dict.fromkeys(ids))
    print("AUTO IDS:", ids[:LIMIT_IDS])
    return ids[:LIMIT_IDS]


# =========================
# DOWNLOAD
# =========================
def download_document(page, eid):
    try:
        page.goto(BASE_URL + "/odluke-o-dodeli-ugovora")
        page.wait_for_load_state("networkidle")
        page.wait_for_selector("tr", timeout=15000)

        rows = page.locator("tr").all()

        for row in rows:
            try:
                row_text = row.inner_text()
            except:
                continue

            if str(eid) in row_text:
                button = row.locator("a, button").first

                with page.expect_download(timeout=15000) as download_info:
                    button.click()

                download = download_info.value
                safe_name = download.suggested_filename.replace("/", "_").replace("\\", "_")
                path = os.path.join(DOCUMENTS_DIR, f"{eid}_{safe_name}")
                download.save_as(path)

                print("DOWNLOADED:", path)

                with open(path, "rb") as f:
                    head = f.read(300)

                lower_path = path.lower()

                if head.startswith(b"%PDF"):
                    return path, "pdf"
                elif b"<?xml" in head or lower_path.endswith(".xml"):
                    return path, "xml"
                elif lower_path.endswith(".docx"):
                    return path, "docx"
                else:
                    return path, "unknown"

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
    except Exception as e:
        print("DOCX READ ERROR:", e)
        return ""


def read_pdf(path):
    text = ""

    if pdfplumber is not None:
        try:
            with pdfplumber.open(path) as pdf:
                for p in pdf.pages:
                    text += p.extract_text() or ""
                    text += "\n"
        except Exception as e:
            print("PDF TEXT READ ERROR:", e)

    if len(text.strip()) < 50:
        try:
            images = convert_from_path(path, dpi=200)
            for img in images:
                text += pytesseract.image_to_string(img, lang="srp+eng")
                text += "\n"
        except Exception as e:
            print("PDF OCR ERROR:", e)

    return text


def read_xml(path):
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        parts = []

        for elem in root.iter():
            if elem.text and elem.text.strip():
                parts.append(elem.text.strip())

        return "\n".join(parts)

    except:
        try:
            return open(path, encoding="utf-8", errors="ignore").read()
        except:
            return ""


# =========================
# ANALYZE HELPERS
# =========================
def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def is_cancelled(text):
    t = text.lower()
    return any(k in t for k in [
        "obustavi postupak",
        "postupak se obustavlja",
        "odluka o obustavi",
        "obustavlja se postupak"
    ])


def extract_prices(text):
    text = text.replace("\xa0", " ")

    matches = re.findall(
        r"\d{1,3}(?:[.\s]\d{3})+(?:,\d{2})?|\d+(?:,\d{2})",
        text
    )

    prices = []

    for m in matches:
        clean = m.replace(" ", "").replace(".", "").replace(",", ".")

        try:
            val = float(clean)

            if 1000 < val < 1_000_000_000:
                prices.append(val)

        except:
            pass

    return sorted(set(prices))


def find_winner(text):
    parts = re.split(r"[.\n]", text)

    company_patterns = [
        "doo",
        "d.o.o",
        "d.o.o.",
        "ad",
        "a.d",
        "a.d.",
        "preduzetnik",
        "pr",
        "javno preduzeće",
        "jp"
    ]

    for i, part in enumerate(parts):
        if "dodeljuje" in part.lower() or "ugovor se dodeljuje" in part.lower():
            for j in range(i, min(i + 5, len(parts))):
                chunk = parts[j].strip()
                low = chunk.lower()

                if any(p in low for p in company_patterns):
                    return chunk

            return part.strip()

    return None


def detect_rejection_reasons(text):
    patterns = [
        "neprihvatljiva ponuda",
        "ponuda se odbija",
        "nije prihvatljiva",
        "ne ispunjava uslove",
        "ne ispunjava kriterijume",
        "diskvalifikovan",
        "nije dostavio",
        "nije dostavljena",
        "ne odgovara",
        "odbijena je ponuda",
        "ponuda nije prihvatljiva"
    ]

    t = text.lower()
    return [p for p in patterns if p in t]


def extract_accepted_price(text):
    parts = re.split(r"[.\n]", text)

    keywords = [
        "dodeljuje",
        "ugovor se dodeljuje",
        "dodeli ugovor",
        "izabrana ponuda",
        "najpovoljnija ponuda",
        "ponuda ponuđača"
    ]

    for i, part in enumerate(parts):
        low = part.lower()

        if any(k in low for k in keywords):
            chunk = " ".join(parts[i:i + 4])
            prices = extract_prices(chunk)

            if prices:
                return max(prices)

    return None


# =========================
# ANALYZE
# =========================
def analyze(text):
    text = clean_text(text)

    if not text:
        return None

    if is_cancelled(text):
        return None

    prices = extract_prices(text)

    if len(prices) < 2:
        return None

    lowest = min(prices)

    accepted = extract_accepted_price(text)

    if accepted is None:
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
        "suspicious": accepted > lowest,
        "rejection_reasons": reasons
    }


# =========================
# MAIN
# =========================
def main():
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        ids = fetch_entity_ids(page)

        for eid in ids:
            print("\nPROCESS:", eid)

            if already_processed(eid):
                print("SKIP - already processed:", eid)
                continue

            path, ext = download_document(page, eid)

            if not path:
                print("NO DOCUMENT:", eid)
                continue

            if ext == "docx":
                text = read_docx(path)
            elif ext == "pdf":
                text = read_pdf(path)
            elif ext == "xml":
                text = read_xml(path)
            else:
                try:
                    text = open(path, encoding="utf-8", errors="ignore").read()
                except:
                    text = ""

            data = analyze(text)

            if data:
                data["id"] = eid
                data["file"] = path
                results.append(data)

            mark_processed(eid)

        browser.close()

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
        total_loss += accepted - lowest
        prices.append(accepted)
        count += 1

    sorted_prices = sorted(prices)
    median = sorted_prices[len(sorted_prices) // 2] if sorted_prices else 0

    stats = {
        "broj_tendera": count,
        "ukupna_vrednost": f"{int(total_value)} RSD",
        "ukupna_vrednost_eur": f"{int(total_value / 117.2)} EUR",
        "broj_ugovora": count,
        "ugovorena_vrednost": f"{int(total_value)} RSD",
        "ugovorena_vrednost_eur": f"{int(total_value / 117.2)} EUR"
    }

    loss = {
        "najbolja_ponuda": f"{int(min(prices)) if prices else 0} RSD",
        "srednja_ponuda": f"{int(median)} RSD",
        "prihvacena_ponuda": f"{int(max(prices)) if prices else 0} RSD",
        "gubitak_prema_najboljoj": f"{int(total_loss)} RSD",
        "gubitak_prema_srednjoj": f"{int(total_loss - median) if prices else 0} RSD",
        "broj_analiziranih": count
    }

    with open("tenders.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    with open("stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    with open("loss-data.json", "w", encoding="utf-8") as f:
        json.dump(loss, f, indent=2, ensure_ascii=False)

    print("DONE")


if __name__ == "__main__":
    main()
