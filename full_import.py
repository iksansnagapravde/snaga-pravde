import os
import re
import json
import requests
import xml.etree.ElementTree as ET

BASE_URL = "https://jnportal.ujn.gov.rs"
os.makedirs("documents", exist_ok=True)

# 🔥 UBACI SVOJ TOKEN OVDE
TOKEN = "OVDE_STAVI_TOKEN"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora"
}

# =========================
# TEST IDS (posle širiš)
# =========================
def fetch_entity_ids():
    return [675152, 670413, 666041]

# =========================
# DOWNLOAD DOKUMENTA
# =========================
def download_document(eid):
    try:
        url = f"{BASE_URL}/GetDocuments.ashx"

        params = {
            "entityId": eid,
            "objectMetaId": 2,
            "documentGroupId": 169,
            "associationTypeId": 1,
            "userToken": TOKEN
        }

        r = requests.get(url, params=params, headers=HEADERS)

        data = r.json()

        if not data:
            print("NO DOCUMENTS")
            return None, None

        file_url = data[0].get("url") or data[0].get("downloadUrl")

        if file_url.startswith("/"):
            file_url = BASE_URL + file_url

        file = requests.get(file_url, headers=HEADERS)
        content = file.content

        if content.startswith(b"%PDF"):
            doc_type = "pdf"
        elif b"<?xml" in content[:200]:
            doc_type = "xml"
        else:
            doc_type = "unknown"

        filename = f"documents/{eid}.{doc_type}"

        with open(filename, "wb") as f:
            f.write(content)

        print(f"DOWNLOADED {doc_type.upper()}:", filename)

        return filename, doc_type

    except Exception as e:
        print("DOWNLOAD ERROR:", e)
        return None, None

# =========================
# XML PARSER (GLAVNI)
# =========================
def parse_xml(path):
    try:
        tree = ET.parse(path)
        root = tree.getroot()

        text = ET.tostring(root, encoding="unicode")

        # izvuci sve cene
        prices = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", text)
        prices = [float(p.replace(".", "").replace(",", ".")) for p in prices]
        prices = sorted(set(prices))

        # firme
        companies = re.findall(r"[A-ZČĆŠĐŽ][A-Za-zČĆŠĐŽčćšđž\s\-]+(доо|a\.d\.)", text)

        winner = companies[0] if companies else None

        if not prices:
            return None

        lowest = min(prices)
        accepted = max(prices)
        second = prices[1] if len(prices) > 1 else None

        return {
            "winner": winner,
            "accepted": accepted,
            "lowest": lowest,
            "second": second,
            "difference": accepted - lowest,
            "suspicious": accepted > lowest
        }

    except Exception as e:
        print("XML ERROR:", e)
        return None

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

        if doc_type != "xml":
            print("SKIP NON XML")
            continue

        result = parse_xml(path)

        if result:
            output.append({
                "id": eid,
                **result
            })

    with open("tenders.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print("\nDONE")

if __name__ == "__main__":
    main()
