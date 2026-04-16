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
# DOWNLOAD
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

    except Exception as e:
        print("ERROR:", e)

    return None


# =========================================
# EXTRACT TEXT
# =========================================
def extract_text(pdf_path):
    text = ""

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"
    except:
        return ""

    return text


# =========================================
# PARSE PRICES
# =========================================
def extract_prices(text):
    prices = []

    # hvata: 1.234.567,89 ili 1234567,89
    matches = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", text)

    for m in matches:
        try:
            num = float(m.replace(".", "").replace(",", "."))
            prices.append(num)
        except:
            pass

    return prices


# =========================================
# ANALYSIS
# =========================================
def analyze(prices):
    if not prices or len(prices) < 2:
        return None

    lowest = min(prices)
    avg = statistics.mean(prices)

    # pretpostavka: zadnja cena = prihvaćena
    accepted = prices[-1]

    return {
        "lowest": lowest,
        "average": avg,
        "accepted": accepted,
        "loss_vs_lowest": accepted - lowest,
        "loss_vs_avg": accepted - avg
    }


# =========================================
# FETCH ENTITY IDs (RUČNO ZA SADA)
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
        print(f"PROCESS: {eid}")

        pdf_path = download_pdf(eid)
        if not pdf_path:
            continue

        text = extract_text(pdf_path)

        if len(text) < 100:
            print("NO TEXT")
            continue

        prices = extract_prices(text)

        print("PRICES:", prices[:5])

        analysis = analyze(prices)

        if analysis:
            results.append(analysis)

    # =====================================
    # SAVE RESULT
    # =====================================
    if results:
        with open("results.json", "w") as f:
            json.dump(results, f, indent=2)

        print("RESULTS SAVED")


if __name__ == "__main__":
    main()
