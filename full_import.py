import requests
import os
import re
import pdfplumber
import statistics
import json

BASE_URL = "https://jnportal.ujn.gov.rs"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

SAVE_FOLDER = "documents"
os.makedirs(SAVE_FOLDER, exist_ok=True)


# =========================================
# DOWNLOAD PDF
# =========================================
def download_pdf(entity_id):
    url = f"{BASE_URL}/GetDocuments.ashx?entityId={entity_id}"

    try:
        r = requests.get(url, headers=HEADERS, timeout=60)

        if r.status_code == 200 and len(r.content) > 2000:
            path = os.path.join(SAVE_FOLDER, f"{entity_id}.pdf")

            with open(path, "wb") as f:
                f.write(r.content)

            print(f"SAVED: {entity_id}")
            return path

        else:
            print(f"FAILED: {entity_id}")

    except Exception as e:
        print("ERROR:", e)

    return None


# =========================================
# EXTRACT TEXT FROM PDF
# =========================================
def extract_text(pdf_path):
    text = ""

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"
    except Exception as e:
        print("PDF ERROR:", e)
        return ""

    return text


# =========================================
# SMART PRICE EXTRACTION
# =========================================
def extract_prices_smart(text):
    prices = []

    lines = text.split("\n")

    keywords = [
        "понуда",
        "вредност",
        "износ",
        "уговор",
        "динара",
        "рсд"
    ]

    for line in lines:
        lower = line.lower()

        if any(k in lower for k in keywords):
            matches = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", line)

            for m in matches:
                try:
                    num = float(m.replace(".", "").replace(",", "."))
                    prices.append(num)
                except:
                    pass

    return prices


# =========================================
# FIND ACCEPTED PRICE (WINNER)
# =========================================
def find_accepted_price(text):
    lines = text.split("\n")

    for i, line in enumerate(lines):
        lower = line.lower()

        if "изабрана" in lower or "најповољнија" in lower:
            for j in range(i, min(i + 5, len(lines))):
                matches = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", lines[j])
                if matches:
                    try:
                        return float(matches[0].replace(".", "").replace(",", "."))
                    except:
                        pass

    return None


# =========================================
# ANALYSIS
# =========================================
def analyze(prices, accepted):
    if not prices or len(prices) < 2:
        return None

    lowest = min(prices)
    average = statistics.mean(prices)

    if not accepted:
        accepted = prices[-1]

    return {
        "najbolja_ponuda": lowest,
        "srednja_ponuda": average,
        "prihvacena_ponuda": accepted,
        "gubitak_prema_najboljoj": accepted - lowest,
        "gubitak_prema_srednjoj": accepted - average
    }


# =========================================
# SAMPLE ENTITY IDS (ZA TEST)
# =========================================
def get_sample_ids():
    return [
        667697,
        668108,
        669001,
        657421
    ]


# =========================================
# MAIN
# =========================================
def main():
    ids = get_sample_ids()

    results = []

    for eid in ids:
        print("===================================")
        print(f"PROCESS: {eid}")

        pdf_path = download_pdf(eid)
        if not pdf_path:
            continue

        text = extract_text(pdf_path)

        if len(text) < 100:
            print("NO TEXT")
            continue

        # DEBUG: vidi linije
        for line in text.split("\n"):
            if "понуда" in line.lower():
                print("LINE:", line)

        prices = extract_prices_smart(text)
        print("PRICES:", prices)

        accepted = find_accepted_price(text)
        print("ACCEPTED:", accepted)

        analysis = analyze(prices, accepted)

        if analysis:
            results.append(analysis)

    # =====================================
    # SAVE JSON
    # =====================================
    if results:
        with open("results.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        print("RESULTS SAVED")
    else:
        print("NO VALID DATA")


if __name__ == "__main__":
    main()
