def download_pdf(tender_id):
    try:
        url = f"https://jnportal.ujn.gov.rs/tender-eo/{tender_id}"

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        driver = webdriver.Chrome(options=options)
        driver.get(url)
        time.sleep(5)

        html = driver.page_source

        # 🔥 OVO JE KLJUČNO – uzmi entityId iz JS
        match = re.search(r'entityId["\']?\s*:\s*(\d+)', html)

        if not match:
            print("NO ENTITY ID:", tender_id)
            driver.quit()
            return None

        entity_id = match.group(1)
        print("ENTITY:", entity_id)

        # 🔥 pravi API (bez prefetch)
        api_url = f"https://jnportal.ujn.gov.rs/get-documents?entityId={entity_id}&objectMetaId=2&documentGroupId=169&associationTypeId=1"

        r = requests.get(api_url, headers=HEADERS, timeout=30)

        if r.status_code != 200:
            print("DOC FAIL:", r.status_code)
            driver.quit()
            return None

        try:
            data = r.json()
        except:
            print("NOT JSON:", tender_id)
            driver.quit()
            return None

        # 🔥 traži PDF
        for doc in data:
            url = doc.get("DocumentUrl")

            if not url:
                continue

            full = "https://jnportal.ujn.gov.rs" + url

            pdf = requests.get(full, headers=HEADERS, timeout=60)

            if pdf.status_code != 200:
                continue

            if not pdf.content.startswith(b"%PDF"):
                continue

            path = f"documents/{tender_id}.pdf"

            with open(path, "wb") as f:
                f.write(pdf.content)

            print("PDF SAVED:", tender_id)
            driver.quit()
            return path

        print("NO PDF:", tender_id)
        driver.quit()
        return None

    except Exception as e:
        print("ERROR:", e)
        return None
