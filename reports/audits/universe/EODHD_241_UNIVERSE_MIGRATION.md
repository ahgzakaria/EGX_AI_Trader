# EODHD 241 Universe Migration

- **Source**: `EODHD exchange-symbol-list/EGX` (authenticated EODHD REST; no HTML scraping, no Yahoo, no manual entry)
- **Retrieved at (UTC)**: `2026-07-30T20:39:42.471332+00:00`
- **Active tickers returned**: **241**
- **Exactly 241**: **yes**
- **Old operational universe**: 265 symbols
- **Common**: 225 · **Removed**: 40 · **Added**: 16
- **Archived (inactive) records retained for history**: 76

Every active record carries the full company name, the official `.EGX` symbol, exchange, currency, instrument type and ISIN where supplied. The retired 265-symbol list is archived under `data/universe/archive/legacy_symbols_265.csv` and is not on any runtime fallback chain.

## Removed symbols

Not eligible for a new entry. Still readable in historical trades, saved runs and reports, and still monitorable while an open paper position exists.

- `ACRO` — Acrow Misr
- `ADRI` — Arab Development & Real Estate Investment
- `AIFI` — Historical / Inactive Symbol
- `AIHC` — Historical / Inactive Symbol
- `ALEX` — Alexandria Cement
- `AMPI` — Al Moasher for Programming and Information Dissemination
- `APPC` — Advanced Pharmaceutical Packaging Co. (APP)
- `BIDI` — El Badr Investment And Development BID
- `BIGP` — Barbary Investment Group ( BIG)
- `DCRC` — Delta Construction & Rebuilding
- `DIFC` — Historical / Inactive Symbol
- `EGX30ETF` — Historical / Inactive Symbol
- `EITP` — Egyptian International Tourism Projects
- `ELWA` — Historical / Inactive Symbol
- `ESAC` — Historical / Inactive Symbol
- `ESRS` — Ezz Steel
- `FCMD` — Historical / Inactive Symbol
- `FIRE` — First Investment Company And Real Estate Development
- `FNAR` — Al Fanar Contracting Construction Trade Import And Export Co
- `GOCO` — Golden Coast Company
- `GTHE` — Global Telecom Holding
- `HCFI` — Historical / Inactive Symbol
- `IBCT` — International Business Corporation For Trading and Agencies
- `INEG` — Integrated Engineering Group S.A.E
- `IRAX` — EL Ezz Aldekhela Steel - Alexandria
- `MISR` — Historical / Inactive Symbol
- `MKIT` — Misr Kuwait Investment & Trading Co.
- `MMAT` — Historical / Inactive Symbol
- `NBKE` — National Bank Of Kuwait- Egypt- NBK
- `NCGC` — Nile Cotton Ginning
- `PACH` — Paint & Chemicals Industries (Pachin)
- `RKAZ` — REKAZ Financial Holding
- `RMTV` — Rowad Misr Tourism Investment
- `SMPP` — Modern Shorouk Printing & Packaging
- `SNFI` — Historical / Inactive Symbol
- `SUCE` — Suez Cement
- `TORA` — Torah Cement
- `UASG` — United Arab Shipping
- `UPMS` — Union Pharmacist Company For Medical Services and Investment
- `VERT` — Vertika for Industry & Trade

## Added symbols

- `AGIG` — Arab Moltaka Investments Co
- `AIND` — Arabia Investments Holding
- `ALRA` — Atlas For Investment and Food Industries
- `AMII` — Arabian Metal Industries And Industrial Investments
- `AUTO` — GB Corp
- `EDBM` — The Egyptian Company for Construction Development-Lift Slab
- `MATD` — Marsa Marsa Alam For Tourism Development
- `MEDP` — Medical Packaging Company
- `NDRL` — National Drilling
- `NULL` — Fitness Prime
- `ORMT` — Orascom Investment Holding
- `PIOH` — Pioneers Holding
- `QNBA` — Qatar National Bank
- `SEIGA` — Saudi Egyptian Investment & Finance $
- `SRWA` — Sarwa Capital Holding
- `VLMR` — Valmore Holding

## Data quality

- Missing company names: _none_
- Duplicate symbols: _none_
- Company names added to retained symbols: 225 (the retired list stored tickers only)

### Ambiguous company names

Distinct EODHD tickers that share one company name. Both remain separate universe records; the pair is flagged for operator review.

- "Fitness Prime" — `FTNS`, `NULL`
- "Medical Packaging Company" — `MEDP`, `MEPA`

## Rubix mapping

- Verified from live feed observation (`CASE~TICKER`): **225**
- Unverified — no feed observation, no key emitted: **16**

Mappings are explicit. A ticker never observed on the Rubix `CASE` feed produces no subscription key; nothing is derived by suffix substitution.

Unverified: `AGIG`, `AIND`, `ALRA`, `AMII`, `AUTO`, `EDBM`, `MATD`, `MEDP`, `NDRL`, `NULL`, `ORMT`, `PIOH`, `QNBA`, `SEIGA`, `SRWA`, `VLMR`

**Operational consequence.** An unverified symbol is a full member of the operational universe — daily refresh, historical loading, scans, selectors, AI analysis and backtests all include it — but it contributes no Rubix subscription key until its mapping is verified against a real feed observation. `build_rubix_subscription_plan` reports these under `unmapped_symbols` rather than fabricating `CASE~TICKER`.
