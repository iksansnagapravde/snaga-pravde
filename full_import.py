import os
import re
import json
import requests
import xml.etree.ElementTree as ET

BASE_URL = "https://jnportal.ujn.gov.rs"
os.makedirs("documents", exist_ok=True)

# 🔴 OBAVEZNO UBACI SVOJE VREDNOSTI
TOKEN = "OVDE_USER_TOKEN"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://jnportal.ujn.gov.rs/odluke-o-dodeli-ugovora",
    # 🔴 UBACI IZ DEVTOOLS (Application -> Cookies)
    "Cookie": "ASP.NET_SessionId=OVDE; .ASPXFORMSAUTH=OVDE"
}

# TEST IDS (posle širiš)
def fetch_entity_ids():
    return [675152, 670413, 666041]

# =========================
# DOWNLOAD
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
        content = r.content

        # DEBUG (ostavi za sada)
        print("FIRST BYTES:", content[:80])

        # DETEKCIJA TIPA
        if content.startswith(b"%PDF"):
            ext = "pdf"
        elif b"<?xml" in content[:200]:
            ext = "xml"
        elif b"<html" in content[:200].lower():
            print("❌ DOBIO HTML → TOKEN/COOKIE NE VALJA")
            return None, None
        else:
            ext = "unknown"

        path = f"documents/{eid}.{ext}"

        with open(path, "wb") as f:
            f.write(content)

        print(f"DOWNLOADED {ext.upper()}:", path)

        return path, ext

    except Exception as e:
        print("DOWNLOAD ERROR:", e)
        return None, None

# =========================
# XML PARSER
# =========================
def parse_xml(path):
    try:
        tree = ET.parse(path)
        root = tree.getroot()

        text = ET.tostring(root, encoding="unicode")

        # izvuci cene
        prices = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", text)
        prices = [float(p.replace(".", "").replace(",", ".")) for p in prices]
        prices = sorted(set(prices))

        if not prices:
            return None

        lowest = min(prices)
        accepted = max(prices)
        second = prices[1] if len(prices) > 1 else None

        return {
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
    results = []

    for eid in ids:
        print("\nPROCESS:", eid)

        path, ext = download_document(eid)

        if not path:
            continue

        if ext == "xml":
            data = parse_xml(path)
        else:
            data = {
                "note": "PDF - parser ide kasnije"
            }

        if data:
            results.append({
                "id": eid,
                **data
            })

    with open("tenders.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("\nDONE")

if __name__ == "__main__":
    main()
