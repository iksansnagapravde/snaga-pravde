import json

kurs_eur = 117.2

# =========================
# OSNOVNI STATISTIČKI PODACI
# =========================

broj_tendera = 18452
ukupna_vrednost_rsd = 128945000000

broj_ugovora = 9674
ugovorena_vrednost_rsd = 96320000000

ukupna_vrednost_eur = round(ukupna_vrednost_rsd / kurs_eur)
ugovorena_vrednost_eur = round(ugovorena_vrednost_rsd / kurs_eur)

def format_broj(x):
    return f"{x:,.0f}".replace(",", ".")

stats = {
    "broj_tendera": broj_tendera,
    "ukupna_vrednost": format_broj(ukupna_vrednost_rsd) + " RSD",
    "ukupna_vrednost_eur": format_broj(ukupna_vrednost_eur) + " EUR",
    "broj_ugovora": broj_ugovora,
    "ugovorena_vrednost": format_broj(ugovorena_vrednost_rsd) + " RSD",
    "ugovorena_vrednost_eur": format_broj(ugovorena_vrednost_eur) + " EUR"
}

with open("stats.json", "w", encoding="utf-8") as f:
    json.dump(stats, f, ensure_ascii=False, indent=2)

# =========================
# KALKULATOR GUBITKA BUDŽETA
# =========================

najbolja_ponuda = 842500000
srednja_ponuda = 910300000
prihvacena_ponuda = 1084500000
broj_analiziranih = 9674

loss_data = {
    "najbolja_ponuda": najbolja_ponuda,
    "srednja_ponuda": srednja_ponuda,
    "prihvacena_ponuda": prihvacena_ponuda,
    "broj_analiziranih": broj_analiziranih
}

with open("loss-data.json", "w", encoding="utf-8") as f:
    json.dump(loss_data, f, ensure_ascii=False, indent=2)
