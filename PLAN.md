# vinatlas — Vivino prototype (MVP danes)

## Context
Greenfield osebni projekt: multi-source baza vin in uporabniških reviewjev (start: Vivino, Goriška Brda) kot podlaga za analize. Trenutni `bwine` folder je prazen (samo pyproject + venv), ni git repozitorija.

**Ključni poslovni vprašanji, ki ju mora projekt odgovoriti zdaj (faza 2, vikend obisk Brd):**
1. Katere vinarje obiskati?
2. Pri njih: kateri dve rdeči in kateri dve beli vini poskusiti — in kateri letnik?

Posledici za podatke: (a) rabimo tip vina — `type_id` na wine objektu (verificirano: 1=rdeče, 2=belo; Sivi Pinot ima type_id=2); (b) rabimo vintage-grain ocene — polnega per-letnik vira ni (stran vina ima letnike brez statistik, `api/wines/{id}/vintages` 404), zato triangulacija: explore zadetki so vintage-grain za listane letnike + `api/wineries/{id}/vintages` (verificirano: per-letnik stats, npr. Kabaj Beli Pinot 2014 = 4.2/141, a nepopoln seznam) + reviewji nosijo letnik, oceno in besedilo.

## Zaklenjene odločitve (grill sejа)
- **Ime**: `vinatlas` — preimenuj folder, pyproject, GitHub repo.
- **Ločitev odgovornosti (uporabnikova zahteva)**: zajem opravi **izključno Python skripta** — Claude Code je samo koordinator (generira kodo, orkestrira, queryja bazo), NE izvaja HTTP klicev na Vivino sam. (Probe-curls med planiranjem so bili samo verifikacija sheme; produkcijska pot je skripta.)
- **Store-raw-then-parse (uporabnikova zahteva, design pattern)**: skripta shrani **celoten, neparšan payload** vsakega odgovora v JSONL (cel JSON, brez izbiranja polj). Parsing/flattening se zgodi šele v DuckDB plasti (naslednja faza). Raw je edina avtoriteta; če kasneje rabimo polje, ki ga danes ne gledamo, je že na disku.
- **Pipeline**: Python → surovi JSONL (immutable, full payload) → DuckDB. **HTTP klient: `curl_cffi` ≥ 0.15.0** (zmagovalec raziskave 2026-06-12: najbolj zrel, requests drop-in, TLS/JA3+JA4 browser impersonation). CloudFront WAF ključa na JA3/JA4 fingerprintu + headerjih + IP reputaciji — naša pot (domači IP + browser TLS fingerprint) je zadostna pri nizkem volumnu. Specifika obhoda, zaklenjena:
  - **Pinnan impersonate target** (`chrome131`), NE floating alias `"chrome"` — issue #500: generični Chrome alias po chrome116 ponekod sproži WAF challenge; fallback `safari`/`firefox` ob 403.
  - **Cookie warming**: en GET homepage v isti `Session` pred API klici (pobere WAF/session cookije).
  - **Session** za connection reuse + auto cookie persistenco.
  - **XHR-correct headerji** (impersonate sam injecta UA/sec-ch-ua/accept-language — teh NE prepisujemo): override samo `Accept: application/json, text/plain, */*`, `Sec-Fetch-Dest: empty`, `Sec-Fetch-Mode: cors`, `Sec-Fetch-Site: same-origin`, `Referer`, `Origin`.
  - **Retry samo na 408/425/429/5xx + connection errors**; 403/404 NE retryamo (signal napačnega fingerprinta/cookijev → zamenjaj target ali warmaj, ne v zanko). Exp backoff + full jitter, honor `Retry-After`, cap ~5.
  - Mehanizmi obhoda so del skripte od začetka, ne naknadni dodatek.
- **Re-run semantika**: zaenkrat en sam zajem ("enkrat zajameš in to je to") — merge/refresh logika se rešuje kasneje, ko bo potrebna. Raw datoteke nosijo fetched_at, da kasnejša odločitev ni blokirana. **Raw-first (uporabnikova odločitev): samo surove tabele + tanki flattening VIEW-i** (`v_wineries`, `v_wines`, `v_reviews` — čisto razpakiranje JSON poti v berljive stolpce, brez grain/conformance odločitev). Dimenzijsko modeliranje (raw→stg→dim/fct, brez martov) odloženo, dokler transformacije na surovih podatkih ne pokažejo potrebe. Brez dlt. Source-agnostično: vsak source svoj `data/raw/<source>/`.
- **Prvi case: vinarji.** Winery objekt v katalog odgovoru (`api/wineries/{x}/wines`) že nosi winery-level statistiko (verificirano: Kabaj ratings_count 7549, ratings_average 4.0, wines_count 26) — winery "dimenzija" pride zastonj s katalogi, poseben endpoint ni potreben. V explore zadetkih winery statistike NI (samo id/name/seo_name).
- **Transformacije**: DuckDB SQL; delo s SQL datotekami v DataGripu (uporabnikov SQL IDE, `open -a "DataGrip"`).
- **Obseg**: samo Goriška Brda (`region_ids[]=734`). **Cilj faze 1: celoten korpus regije** — referenca 134 vinarjev / 1358 vin iz `api/regions/734`, ne samo explore zadetki. Razširitev na celo Slovenijo kasneje = sprememba parametra sweepa. Jedro: vsa vina z agregatnimi ocenami (avg + count); besedila reviewjev prva stran (50/vino) za **vsa** vina iz katalogov. **Briškemu vinarju obdržimo vsa vina ne glede na regijsko oznako vina** (enota priporočila je vinar; regija ostane stolpec za filtriranje v queryju).
- **Faza 2** (po zajemu): uporabnik poda kriterije/seznam vinarjev, Claude Code dela querye na bazo in priporoči.
- **Endpoint politika** (uporabnikova zahteva): naslanjati se izključno na skupnostno znane endpointe (uporabnikova tabela + GitHub wrapperji + danes verificirani) — pred implementacijo še enkrat preveriti GitHub za novejše primere (agent v teku).
- **Agent dostop**: Claude Code + `duckdb` CLI + repo CLAUDE.md s shemo. Brez MCP serverja.
- **GitHub**: private repo na osebnem GitHubu (`gh`), podatki gitignored (samo koda + SQL + CLAUDE.md v git).
- **Brez vizualizacije/searcha danes** — naslednja iteracija.

### Amandmaji (grill seja 2 — fetch.py review, 2026-06-12)
- **Fail loud na shape drift (uporabnikova odločitev)**: 200 odgovor z manjkajočim pričakovanim ključem ali ne-JSON telesom → takojšen `FetchAbort` (exit 2). Surovi envelope se zapiše na disk PRED validacijo, da je sporni payload vedno na voljo. 404 ostane toleriran logiran skip pri winery/reviews entitetah (pričakovan status, ne drift); ne-200 na `regions`/`explore` je fatalen; 0 seedanih vinarjev je fatalno. Prazna lista (`wines: []`) je veljavna, ne napaka.
- **Partial run datoteke ob abortu ostanejo** — immutable raw, nikoli brisati; view-i dedupajo by latest `fetched_at`, abort-evidence ostane za diagnozo.
- **Recovery po abortu = poln refetch** — brez resume logike (konsistentno z odloženo merge/refresh semantiko); abort pomeni code fix, potem čist poln run.
- **`add-winery` / HTML search UMAKNJEN iz MVP** (uporabnikova preusmeritev: "search tackle later") — lookup-on-miss (tier 3 strategije pokritosti) se načrtuje kasneje; omembe add-winery drugje v tem dokumentu veljajo kot odloženo, ne kot današnji scope.

## Preverjen Vivino dostop (raziskano in testirano v živo 2026-06-12)
Uradnega API ni; neuradni JSON endpointi delujejo s plain `requests` z domačega IP (CloudFront, brez JS challenga; datacenter IPji blokirani — poganjati lokalno):
- `GET https://www.vivino.com/api/explore/explore` — iskanje; **`country_codes[]=si`** = poreklo (446 zadetkov / 315 vin / 86 vinarjev), `country_code` (singular) = market — NE uporabiti za poreklo. `region_ids[]=734` = Goriška Brda. **Poln sweep verificiran (9 strani): 211 vintage zadetkov = 130 distinct vin = 28 distinct vinarjev** — vsa velika imena so med njimi (Movia, Edi Simčič, Marjan Simčič, Kabaj, Ščurek, Erzetič, Blažič, Medot, Quercus, Valter Sirk, Štekar, Krasno/Klet Brda…). US market (currency_code=USD) vrne identičnih 211 — meja ni odvisna od marketa, en sweep zadošča. Paginacija `page=N` (~25/stran), total v `records_matched`. Shape: `explore_vintage.matches[].vintage.wine.{id,name,winery{id,name,seo_name}}` + `vintage.statistics.{ratings_average,ratings_count}`; winery objekt tu NIMA statistike.
- `GET https://www.vivino.com/api/regions/{id}` — **nov, v skupnostni dokumentaciji neznan endpoint** (verificiran): metapodatki regije + `statistics`: Brda = **134 vinarjev, 1358 vin**. To je referenčna številka za pokritost. `api/regions/{id}/wineries` NE obstaja (404) — endpointa za enumeracijo vseh vinarjev regije ni; tudi HTML stran regije ne vsebuje vgrajenega seznama (client-rendered).
- `GET https://www.vivino.com/api/wineries/{id_ali_seo_ime}/wines` — celoten katalog vinarja (polnejši od explore; Movia id 3685: 43 vin; **deluje tudi s seo-imenom**: `api/wineries/kabaj/wines` → 26 vin, numerični winery_id v odgovoru). Keys `{wines, vintages}`, vino ima `statistics.ratings_average/ratings_count`, **winery objekt nosi winery-level `statistics.{ratings_count, ratings_average, labels_count, wines_count}`** — vir za winery dimenzijo. Params: `start_from`/`limit`/`sort` (skupnostna dok.). `api/wineries/{id}` (detajli) NE obstaja (404).
- `GET https://www.vivino.com/search/wineries?q=...` — HTML, ampak enostavno parsljiv: rezultat vsebuje linke `/en/wineries/{seo_ime}` → resolucija poljubnega imena vinarja v seo-ime za katalog endpoint.
- `GET https://www.vivino.com/api/wines/{id}/reviews?per_page=50&page=N` — flat `{reviews:[...]}`, review: `id, rating, note, language, created_at, user, vintage{year}, activity.statistics.likes_count`. Paginira do prazne strani; opcijsko `language=`.
- `api.vivino.com` (mobilni host) — skupnostno znani: `wines/{id}/tastes`, novejši versioned `api.vivino.com/v/9.x/{wines|vintages}/{id}` (vir: tcvdh/wineLib, maj 2026); neverificirani, za MVP nepotrebni.
- `GET https://www.vivino.com/api/wineries/{id}/vintages` — **verificiran v živo**: per-letnik statistika (Kabaj Beli Pinot 2014 → 4.2/141), a nepopoln seznam (8 zapisov za 26 vin). Del zajema. `api/wines/{id}/vintages` NE obstaja (404); stran vina ima letnike vgrajene brez statistik.
- **GitHub re-check (agent, 2026-06-12)** — dodatni skupnostno dokumentirani endpointi za kasneje: `api/wines/{id}/latest_reviews`, `api/prices?vintage_ids[]=`, `api/vintages/{id}/highlights`, `PUT api/ship_to/` (menjava market konteksta). Viviner param dicti še vedno točni; pozor: aptash/vivino-api je 2020-era koda (zadnji push 2020-11), ne "maj 2026". JSON autocomplete/typeahead za vinarje NE obstaja — search ostaja HTML. Potrjeno (mrbridge powerful scraper): `api/wineries/{id}/wines` NI market-gated — vrne tudi vina brez listinga, zato je to pot do polnega kataloga. Vse `www.vivino.com/api` poti vračajo 403 z datacenter IPjev — zajem teče izključno lokalno z domačega IPja.
- Algolia indeks (`9TAKGWJUXL`) je mrtev — ne graditi nanj.
- Sitemap: `vivino.com/sitemap.xml` → gzip pod-sitemapi `nsm/wineries_N_<lang>.xml.gz` — globalna enumeracija vseh winery URL-jev (seo-imena). Brez regijske informacije, zato ni direkten regijski vektor; uporabno kasneje za offline resolucijo imen.
- Rate-limiting ni opažen (CloudFront, ne Cloudflare); **previdnost na uporabnikovo zahtevo**: ≤1 req/s z jitterjem, browser User-Agent, exponential backoff na 429/403, circuit-breaker (prekini run ob zaporednih 403). Brda korpus ≈ 28 katalogov + ~150–700 review strani — nekaj minut ob 1 req/s.
- Vzorčni odgovori za shemo: `/tmp/vivino_si_origin.json`, `/tmp/vivino_reviews50.json`, `/tmp/vivino_winery.json`, `/tmp/vinatlas_region734.json`, `/tmp/vinatlas_kabaj_wines.json`, `/tmp/vinatlas_brda_p1..p9.json`.

## Strategija pokritosti vinarjev (uporabnikov pomislek, potrjen z meritvami)
Cilj je celoten korpus regije (134 vinarjev / 1358 vin po `api/regions/734`), explore sweep pa najde samo **28 vinarjev / 130 vin**. Endpointa za enumeracijo vseh vinarjev regije ni (404 na `api/regions/734/wineries`, HTML stran regije brez vgrajenega seznama, sitemap brez regij). Tritirna strategija:
1. **Seed**: explore sweep `region_ids[]=734` → 28 vinarjev (vsa velika imena — pokriva uporbanikov "po mojem so ta glavni gor").
2. **Razširitev prek katalogov**: za vsakega od 28 vinarjev `api/wineries/{id}/wines` → celoten katalog vključno z neocenjenimi/nelistanimi vini (Movia 43, Kabaj 26 vs. explore podmnožica) + winery-level statistika. Pričakovano skupaj nekaj sto vin — poročati dejansko pokritost vs. 1358.
3. **Lookup-on-miss** (`add-winery <ime>`): `search/wineries?q=` (HTML parse linka `/en/wineries/{seo}`) → `api/wineries/{seo}/wines` → ingest. Garantira pokritost vsakega vinarja z uporabnikove vikend liste, tudi izven explore rezultatov.
- Preverjene slepe ulice: region payload NE vsebuje seznama vinarjev (samo counte — verificirano na celotnem JSON-u); JSON autocomplete za vinarje ne obstaja; multi-market sweep ne razširi nabora (US = identičnih 211).
- Dolgi rep (~106 malih vinarjev brez ocenjenih vin) za MVP sprejmemo kot nepokrit; kasnejša iteracija: zunanji seznam briških vinarjev (npr. konzorcij Brda) + `add-winery` batch, ali wineries sitemap matching (globalni `nsm/wineries_N_*.xml.gz` + hidracija prek katalogov).

## Struktura projekta
```
vinatlas/
  pyproject.toml            # name=vinatlas; deps: curl_cffi>=0.15.0, duckdb; uv
  CLAUDE.md                 # shema baze, duckdb CLI navodila, refresh ukazi
  .gitignore                # data/, .venv/, .idea/
  src/vinatlas/
    vivino/fetch.py         # zajem -> data/raw/vivino/ (parsanje v Python skriptah)
    build_db.py             # raw JSONL -> data/vinatlas.duckdb (požene sql/)
  sql/
    raw_vivino.sql          # raw tabele: read_json_auto nad JSONL (explore, winery_wines, winery_vintages, reviews, regions) — 1:1 s surovimi odgovori
    views_vivino.sql        # tanki flattening view-i: v_wineries (winery grain + winery statistics iz katalogov), v_wines (wine grain: ocene avg/count, type_id, winery, region), v_vintages (vintage grain: year + stats iz explore zadetkov in winery_vintages), v_reviews (review grain: rating, note, language, vintage year) — brez modeliranja, samo razpakirane JSON poti
  data/                     # gitignored
    raw/vivino/{regions,explore,winery_wines,winery_vintages,reviews}/*.jsonl   # cel neparšan payload
    vinatlas.duckdb
```

## Koraki
1. **Preimenovanje**: `mv /Users/matej/dev/personal/bwine /Users/matej/dev/personal/vinatlas`; pyproject `name = "vinatlas"`; stari `.venv` zbrisati (embedded poti) in rekreirati z `uv sync`/`uv venv`. Pozor: cwd seje kaže na staro pot — po preimenovanju delati izključno z absolutnimi potmi na novo lokacijo.
2. **Git + GitHub**: `git init`, `.gitignore`, initial commit; `gh repo create vinatlas --private --source . --push` (osebni GitHub).
3. **Zajem** (`fetch.py`): pet korakov v enem CLI — (a) `api/regions/734` za referenčne statistike regije; (b) explore sweep `country_codes[]=si&region_ids[]=734` (Goriška Brda) po straneh do `records_matched` (vintage-grain ocene listanih letnikov); (c) za vsak odkriti winery_id katalog `wineries/{id}/wines` (vina + type_id + winery statistika); (d) `wineries/{id}/vintages` (per-letnik ocene, ~28 requestov); (e) za vsako vino prva stran reviewjev `per_page=50`. Dodatno subcommand `fetch.py add-winery <ime>` za lookup-on-miss (search/wineries HTML → seo-ime → katalog → reviewji). Region/country parametra konfigurabilna (CLI flag), da je razširitev na celo SI samo sprememba klica. Vsak odgovor kot JSONL vrstica `{fetched_at, endpoint, params, http_status, payload}` — `payload` je **cel neparšan JSON**, brez izbiranja polj (store-raw-then-parse). V `data/raw/vivino/<entity>/`. CloudFront-bypass plast (zaklenjeno zgoraj): curl_cffi Session + pinnan `chrome131` + cookie warming + XHR headerji + retry samo na transient kodah. Politeness: sleep ~1 s z jitterjem med klici. Flag `--limit` za smoke test. Zajem izvaja skripta, ne Claude Code.
4. **DuckDB build** (`build_db.py`): idempotenten CREATE OR REPLACE — raw tabele (`read_json_auto` nad JSONL, 1:1 s surovimi odgovori, vse transformacije možne direktno na JSON-ih) + tanki flattening view-i (`v_wineries` z winery statistiko iz katalogov, `v_wines` na wine grainu, `v_reviews`). Dimenzijsko modeliranje odloženo — začne se šele, ko transformacije na raw pokažejo potrebo (takrat raw→stg→dim/fct, brez martov).
5. **CLAUDE.md** (brez skilla — uporabnikova odločitev, formalizira se po prvih realnih uporabah): shema raw tabel in view-ov z opisi, primeri `duckdb data/vinatlas.duckdb "SELECT ..."`, napotek za fazo 2 (fuzzy match imen z ILIKE/`jaro_winkler`, winery statistika + ocene vin + besedila reviewjev → priporočilo), navodilo za `add-winery` ob manjkajočem vinarju. DataGrip: `.sql` datoteke odpirati z `open -a "DataGrip"`, DuckDB povezava za ad-hoc transformacije.
6. **Commit + push** po vsakem smiselnem koraku.

## Verifikacija (end-to-end)
1. Smoke: `fetch.py --limit 2` → preveri JSONL shape.
2. Poln Brda zajem (~5–15 min) → build → sanity: 28 vinarjev iz explore seeda v `v_wineries` z winery statistiko (Kabaj ≈ 4.0 / ~7549 ocen kot referenčna točka), vsa velika imena prisotna (Movia, Edi Simčič, Marjan Simčič, Kabaj, Ščurek); poročilo pokritosti: n vinarjev vs. 134 in n vin vs. 1358 iz `api/regions/734`.
3. Lookup-on-miss test: `fetch.py add-winery <vinar, ki ga ni v explore rezultatih>` → rebuild → vinar v `v_wineries`.
4. Vintage sanity: za top vino (npr. Kabaj Beli Pinot) obstajajo per-letnik podatki iz vsaj enega vira (v_vintages ali reviewji z letnikom).
5. Pravi test (faza 2, interaktivno): uporabnik poda vzorčni seznam Brda vinarjev, Claude Code queryja bazo po CLAUDE.md napotkih in odgovori na obe poslovni vprašanji — (1) ranked priporočilo vinarjev, utemeljeno z reviewji, ne samo s povprečji; (2) 2 rdeči + 2 beli vini z letnikom; manjkajoči vinarji se dodajo z `add-winery`.
