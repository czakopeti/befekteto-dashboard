# Befektető Dashboard – Beállítási Útmutató

## Mit csinál ez a rendszer?
Hetente egyszer, péntek este dupla kattintasz a `FRISSITES_AUTO.bat` fájlra.
A script automatikusan letölti az összes adatot 8 forrásból, kiszámolja a
belépési score-t és a korrekció kockázatát, majd megnyitja a dashboardot.
**Semmi manuális bevitel nem szükséges.**

---

## Első beállítás (egyszer, ~15 perc)

### 1. Python telepítése
- https://python.org/downloads
- Telepítéskor: **pipáld be "Add Python to PATH"**

### 2. FRED API kulcs (ingyenes, 5 perc)
A hozamgörbe és HY credit spread adatokhoz kell.

1. Menj: https://fred.stlouisfed.org/docs/api/api_key.html
2. Kattints: **"Request API Key"**
3. Regisztrálj (ingyenes, email elég)
4. Kapsz egy API kulcsot (pl: `abc123def456...`)
5. Nyisd meg az `auto_update.py` fájlt szövegszerkesztővel
6. Cseréld le: `FRED_API_KEY = "YOUR_FRED_API_KEY_HERE"` → saját kulcsra

### 3. Első futtatás
Dupla kattintás: `FRISSITES_AUTO.bat`

Az első futtatás kicsit lassabb (csomagok telepítése ~2 perc).
Utána minden futtatás 30-60 másodperc.

---

## Heti használat (pénteken)

**Dupla kattintás → `FRISSITES_AUTO.bat` → kész.**

A böngésző automatikusan megnyílik a friss dashboarddal.

---

## Adatforrások – hol veszi az adatokat?

| Adat | Forrás | Ingyenes? |
|---|---|---|
| SPX ár + MA200 | Yahoo Finance (yfinance) | ✅ Igen |
| VIX | Yahoo Finance (yfinance) | ✅ Igen |
| EPS revision score | S&P Global xlsx | ✅ Igen |
| Hozamgörbe (10Y–2Y) | FRED API | ✅ Igen (API kulcs kell) |
| HY Credit Spread | FRED API | ✅ Igen (API kulcs kell) |
| Piaci Breadth | Yahoo Finance (számítás) | ✅ Igen |
| Put/Call arány | CBOE website | ✅ Igen |
| AAII Sentiment | AAII website | ✅ Igen |

---

## A dashboard mit mutat?

**Belépési score (0–100):**
- ≥ 65: 🟢 Most érdemes befektetni
- 40–64: 🟡 Várj pár hetet
- < 40: 🔴 Ne fektess be most

**Korrekció kockázat (0–100%):**
- ≥ 60%: 🟣 Fontolja meg a kilépést
- 35–59%: 🟡 Figyelj
- < 35%: ✅ Nincs figyelmeztetés

**Visszavásárlás trigger:**
Ha kilépési jel volt és az SPX ≥10%-ot esett:
- VIX csökkeni kezd (2 egymást követő hét)
- Belépési score ≥ 50 visszaemelkedik
- AAII bearish > 45%

---

## FY26 EPS estimate frissítése (havonta 1x)

Az `auto_update.py` fájl tetején:
```python
FY26_EPS_EST = 315.0   # ← ezt frissítsd havonta
```
Az aktuális értéket a FactSet Earnings Insight PDF-ben találod
(google: "FactSet Earnings Insight" → ingyenes PDF, heti kiadás).

---

## Ha valami nem működik

**FRED adatok nem jönnek be:**
→ Ellenőrizd az API kulcsot az auto_update.py-ban

**CBOE/AAII scraping megváltozott:**
→ A script fallback értékeket használ és folytatja
→ Írj nekem, frissítem a scriptet

**EPS xlsx nem töltődik le:**
→ Manuálisan töltsd le: spglobal.com/spdji → SP 500 EPS estimates
→ Másold a könyvtárba `sp500_eps_raw.xlsx` névvel

---

*Nem befektetési tanácsadás · Buy & hold timing eszköz*
