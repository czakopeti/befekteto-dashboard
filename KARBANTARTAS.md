# 📋 Befektető Dashboard v3 – Karbantartási Kézikönyv

> **TL;DR:** Heti 1 kattintás (FRISSITES_AUTO.bat) + havonta 5 perc kézi frissítés.
> Ez a dokumentum pontosan megmondja mikor mit kell csinálni.

---

## 🔴 EGYSZERI BEÁLLÍTÁS (csak egyszer, már megvan?)

| Teendő | Hol | Státusz |
|--------|-----|---------|
| Python telepítése | python.org/downloads | ☐ |
| FRED API kulcs (ingyenes) | fred.stlouisfed.org/docs/api/api_key.html | ☐ |
| GitHub repo létrehozása | github.com/new (Public) | ☐ |
| FRED_API_KEY beállítása GitHub Secrets-be | Repo → Settings → Secrets | ☐ |
| GitHub Pages bekapcsolása | Repo → Settings → Pages → main branch | ☐ |
| Első futtatás | FRISSITES_AUTO.bat dupla kattintás | ☐ |

---

## 🟢 HETI TEENDŐK (automatikus – nem kell csinálni semmit)

**Minden pénteken 20:00-kor** a GitHub Actions automatikusan:
1. Letölti az összes adatot (SPX, VIX, EPS, FRED, CBOE, AAII, CNN, Skew)
2. Kiszámolja a belépési score-t és korrekció kockázatot
3. Frissíti a dashboardot a GitHub Pages URL-eden
4. Ha hiba van: emailt küld

**Neked csak meg kell nézni** a dashboardot mielőtt befektetsz.

---

## 🟡 HAVI TEENDŐK (5 perc, minden hónap 1. hétvégéjén)

### EPS becslés frissítése (LEGFONTOSABB)

**Forrás:** google → "FactSet Earnings Insight" → letöltöd az ingyenes PDF-et
**Keresési szó a PDF-ben:** "bottom-up EPS estimate" vagy "CY 2026"

**Mit kell módosítani az `auto_update.py`-ban:**
```python
FY26_EPS_EST = 338.0   # ← ezt a számot frissítsd
```

**Frissítési napló – töltsd ki havonta:**
```
2026.01: 315
2026.02: 325
2026.03: 338
2026.04: ___  ← következő
2026.05: ___
2026.06: ___
```

**Hogyan találod meg az értéket a PDF-ben:**
- Nyisd meg a FactSet Earnings Insight PDF-et
- Keress rá: "bottom-up EPS estimate" vagy "S&P 500 EPS"
- A "CY 2026" sor melletti szám az, amit be kell írni
- Ha nem találod: a "forward 12-month EPS" × 1.0–1.05 is jó közelítés

---

## 🔵 NEGYEDÉVES TEENDŐK (30 perc, minden negyedév végén)

### Audit: jól működtek-e a jelzések?

1. Nyisd meg a `history.json` fájlt
2. Nézd meg visszamenőleg: amikor `corrProb >= 60%` volt, esett-e utána a piac?
3. Amikor `entryScore >= 65` volt, emelkedett-e utána?

**Ha a jelzések pontatlanok voltak, finomíts:**
```python
# calc_corr_prob-ban – ha sok volt az "áljelzés" (corrProb magas de nem esett)
if rec > 20: p += 25   # csökkentsd 20-ra ha túl érzékeny
elif rec > 12: p += 12  # emeld 15-re ha nem elég érzékeny

# calc_entry_score-ban – ha túl ritkán adott vételi jelet
s += (10 if pc > 1.15 else ...)  # emeld az értékeket ha túl konzervatív
```

---

## 🟠 FÉLÉVES / ÉVES TEENDŐK

### PE_FAIR_VALUE felülvizsgálata

```python
PE_FAIR_VALUE = 19.5   # ← ez a "fair" P/E baseline
```

**Mikor kell emelni:**
- Ha a 10 éves kötvényhozam tartósan 3% alatt marad (alacsony kamat = magasabb P/E)
- Ha a forward P/E 2 éven át 22x+ felett van anélkül hogy korrekció jönne
- Iránymutatás: 2027-2028-ban ha tartósan 22x+ → emeld 20.0-20.5-re

**Mikor kell csökkenteni:**
- Ha a Fed tartósan 5%+ kamatszinten marad
- Ha recesszió következik be és az EPS várakozások tartósan csökkennek

### A kód scraping részeinek ellenőrzése

A CBOE és AAII oldalak néha megváltoztatják a HTML struktúrát, ami megtörheti a scrapinget. Jelei:
- `error_log.json`-ban `Put/Call` vagy `AAII` hibák sorozatosan
- A dashboard fallback értékeket mutat heteknél

**Mit csinálj:** Nyisd meg az `auto_update.py`-t és keresd meg a `fetch_put_call()` és `fetch_aaii()` függvényeket – a regex mintákat kell frissíteni.

---

## 🔴 AZONNALI TEENDŐK (ha hiba van)

### GitHub Actions email jött – mi a teendő?

1. Menj: `github.com/NEVED/sp500-tracker/actions`
2. Kattints az utolsó futtatásra
3. Töltsd le az `error-log-XXXXX` artifactot
4. Nézd meg melyik forrás hibázott

**Tipikus hibák és megoldásuk:**

| Hiba | Ok | Megoldás |
|------|-----|----------|
| `FRED API kulcs hiányzik` | GitHub Secret nem lett beállítva | Repo → Settings → Secrets → FRED_API_KEY |
| `EPS parsing hiba` | S&P Global xlsx struktúra változott | Töltsd le manuálisan, nézd meg a lapnevet |
| `AAII nem elérhető` | Website változott / timeout | Fallback érték fut, nem kritikus |
| `CBOE P/C nem található` | HTML struktúra változott | Frissítsd a regex mintát |
| `FRED 429 Too Many Requests` | Rate limit | Várj 1 órát, újra fut |

### Ha az egész script nem fut le

```bash
# Futtatsd manuálisan debug módban:
python auto_update.py

# Ha Python csomaghiba:
pip install yfinance requests pandas openpyxl --upgrade
```

---

## 📅 EMLÉKEZTETŐ NAPTÁR

Ajánlott: állíts be ismétlődő emlékeztetőt a telefonodba.

```
📅 Minden hónap 1. szombat: EPS frissítés (5 perc)
📅 Minden pénteken este:    Dashboard megnézése (2 perc)
📅 Március 31, Jún 30, Szept 30, Dec 31: Negyedéves audit (30 perc)
📅 Szeptember 1:            PE_FAIR_VALUE felülvizsgálat (éves)
```

---

## 🔧 GYORS REFERENCIA – Fő konfigurációs értékek

```python
# auto_update.py elején:
FY26_EPS_EST  = 338.0   # ← HAVONTA FRISSÍTSD
PE_FAIR_VALUE = 19.5    # ← ÉVENTE FELÜLVIZSGÁLD
FRED_API_KEY  = "..."   # ← GitHub Secrets-ben van (ne írd ide!)

# MY_STOCKS – saját részvények listája:
MY_STOCKS = [
    ("AAPL", "Apple"),      # ← cseréld a saját részvényeidre
    ("MSFT", "Microsoft"),
    ("NVDA", "Nvidia"),
]
```

---

## 📊 MIT JELENT A DASHBOARD – GYORS DÖNTÉSI FA

```
Belépési score ≥ 65 + Korrekció kockázat < 30%
  → 🟢 FEKTESS BE – Kelly allokáció szerint (pl. 60% SPX)

Belépési score 40–65
  → 🟡 FELEZD MEG – fele most, fele ha score 65+ lesz

Belépési score < 40
  → 🔴 TARTSD VISSZA – jobb ár jön

Korrekció kockázat ≥ 60%
  → 🟣 KILÉPÉS MÉRLEGELÉSE – ha már pozícióban vagy

SPX –10% a csúcstól + score visszaemelkedik ≥ 50
  → 🔄 VÁSÁROLJ VISSZA – ez volt az egész terv
```

---

## 🔗 Hasznos linkek

| Link | Mire való |
|------|-----------|
| [FactSet Earnings Insight](https://www.factset.com/earningsinsight) | Heti ingyenes EPS PDF |
| [FRED T10Y2Y](https://fred.stlouisfed.org/series/T10Y2Y) | Hozamgörbe aktuális |
| [FRED RECPROUSM156N](https://fred.stlouisfed.org/series/RECPROUSM156N) | Recessziós valószínűség |
| [CNN Fear & Greed](https://www.cnn.com/markets/fear-and-greed) | Hangulat index |
| [CBOE Market Stats](https://www.cboe.com/us/options/market_statistics/) | Put/Call arány |
| [AAII Survey](https://www.aaii.com/sentiment-survey) | Befektetői hangulat |
| [FRED API Key](https://fred.stlouisfed.org/docs/api/api_key.html) | Ha új kulcs kell |

---

*Befektető Dashboard v3 · Nem befektetési tanácsadás · Buy & hold timing eszköz*
