# 🍷 Vinjagt

Scanner danske vinforhandleres webshops for gode tilbud, vurderer dem mod uafhængige pris- og kvalitetskilder
og viser resultatet som en hjemmeside med sortering og filtre. Kører på en Raspberry Pi 3B.

- **Tilbud** – sortér efter samlet score, rabat, pris ved køb af 1 eller 6 flasker, kvalitet, værdi for pengene, navn eller nyeste fund. Filtrér på vintype, land, forhandler, maks. pris og fritekst. Filtre gemmes i URL'en, så de kan bogmærkes.
- **Forhandlere** – oversigt over de webshops, der scannes, med platform, status, antal vine og tilbud.
- **Kvalitetskilder** – oversigt over de vurderinger (anmeldere, medier, Vivino, egne CSV), der bruges, og hvor mange vine hver kilde vurderer.
- **Sådan vurderes** – forklaring af metoden.

## Sådan virker det

```
retailers.yaml ──► scan ──► normalisering ──► SQLite (pris pr. dag) ──► vurdering ──► site/ (HTML + JSON) ──► git-gren "pages"
                   │                                                    ▲
                   ├ Shopify:     /products.json (hele kataloget)       ├ markedspris hos de andre forhandlere
                   ├ WooCommerce: /wp-json/wc/store/v1/products         ├ egen prishistorik (90 dage)
                   └ Andre:       sitemap.xml + schema.org på           ├ forhandlerens førpris (krydstjekket)
                                  produktsiderne (roteres over nætter)  └ anmelderpoint, danske medier, Vivino*, egne CSV
```

**Tilbud først:** Scanneren finder selv hver forhandlers tilbudssider (links som "Tilbud", "Udsalg", "Kampagne" på
forsiden) og henter alle produkter derfra hver nat, inkl. undersider. Derefter hentes højst `background_pages` (100)
almindelige produkter, som bruges som markedspris for de andre forhandleres tilbud. Siden viser som standard kun
forhandlernes egne tilbud. Se hvad der findes med `python3 -m vinjagt offers`, og ret med `offer_urls` i `retailers.yaml`.

Platformen findes automatisk for hver forhandler. Vinene normaliseres (pris pr. 75 cl, pris ved 6 fl., årgang,
type, land, anmelderpoint), og samme vin genkendes på tværs af shops via navn/producent/årgang/stregkode.
Hvert tilbud får en score 0–100: 45 % rabat · 35 % værdi for pengene · 20 % kvalitet. Detaljer i `web/metode.html`.

\* Vivino er slået fra som standard – se [Kvalitetskilder](#kvalitetskilder).

## Installation på Raspberry Pi

Kræver Raspberry Pi OS (Bookworm eller Bullseye). Alt bruger færdige Debian-pakker, så intet skal kompileres.

```bash
git clone <dit-repo-url> vinjagt
cd vinjagt
bash deploy/install.sh
nano config.yaml                      # skriv din e-mail i user_agent
sudo systemctl start vinjagt.service  # første kørsel – tager 1-3 timer
```

Installationen opretter:

| Tjeneste              | Hvad                                                     |
|-----------------------|----------------------------------------------------------|
| `vinjagt.timer`       | kører scanning + vurdering + byg + udgiv hver nat 04:15 |
| `vinjagt-web.service` | serverer siden på `http://<pi-ip>:8080`                   |

Nyttige kommandoer:

```bash
python3 -m vinjagt status                 # status pr. forhandler
python3 -m vinjagt detect                 # find platform for alle forhandlere
python3 -m vinjagt scan --only supervin   # scan én forhandler
python3 -m vinjagt build                  # byg siden igen uden at scanne
journalctl -u vinjagt -f                  # følg loggen
```

**Ressourcer på Pi 3B:** typisk 100–250 MB RAM under scanning, få MB disk pr. måned. Shopify/WooCommerce-shops
scannes på få minutter. Øvrige shops hentes side for side (`max_pages_generic`, standard 350 pr. nat) – store shops
dækkes derfor fuldt over nogle nætter, og tilbudssider (`offer_urls`) hentes altid først.

## Udgiv hjemmesiden (GitHub, Codeberg, GitLab, Gitea …)

Kildekoden ligger på grenen `main`. Den byggede side skubbes af Pi'en til grenen `pages` – med ét commit hver gang,
så repository'et ikke vokser med daglige datafiler.

1. Opret et tomt repository hos den tjeneste, du vil bruge, og skub koden:
   ```bash
   git remote add origin git@github.com:BRUGER/vinjagt.git   # eller codeberg.org / gitlab.com / egen Gitea
   git push -u origin main
   ```
2. Giv Pi'en lov til at skubbe – fx med en deploy-nøgle:
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/vinjagt -N ""
   cat ~/.ssh/vinjagt.pub      # tilføj som "Deploy key" med skriveadgang i repo-indstillingerne
   printf 'Host github.com codeberg.org gitlab.com\n  IdentityFile ~/.ssh/vinjagt\n' >> ~/.ssh/config
   ```
3. I `config.yaml`:
   ```yaml
   publish:
     enabled: true
     remote: git@github.com:BRUGER/vinjagt.git
     branch: pages
   ```
4. Slå Pages til:
   - **GitHub:** Settings → Pages → Deploy from a branch → `pages` / `(root)`. Siden kommer på `https://BRUGER.github.io/vinjagt/`.
   - **Codeberg:** grenen `pages` udgives automatisk på `https://BRUGER.codeberg.page/vinjagt/`.
   - **GitLab:** grenen indeholder en `.gitlab-ci.yml`, som udgiver den via GitLab Pages.
   - **Egen Gitea/Forgejo på Pi'en:** brug bare den lokale webserver på port 8080.

Vil du ikke udgive offentligt, så lad `publish.enabled: false` stå – siden er stadig tilgængelig på dit lokalnet.

## Prisalarm på mobilen

Vinjagt kan sende en besked via [ntfy](https://ntfy.sh) (gratis app til iPhone/Android, ingen konto), når der
dukker nye tilbud op: tilbud med høj samlet score og nye tilbud på vine, du holder øje med. Udfyld `alerts` i
`config.yaml` – vælg et emnenavn, der er svært at gætte, og abonnér på det i appen:

```yaml
alerts:
  ntfy_topic: vinjagt-k7q2xm9
  min_score: 80
  watch:
    - {q: "barolo", max: 250}      # nye tilbud på Barolo til højst 250 kr.
```

Samme tilbud meldes kun én gang. På hjemmesiden kan man desuden markere vine som ♥ favoritter (gemmes i browseren).

## Forhandlere

Listen står i `retailers.yaml` (23 danske vinforhandlere med webshop). Tilføj en ny sådan:

```yaml
- name: Min Vinhandel
  url: https://www.minvinhandel.dk
  city: Aarhus
  offer_urls: [/tilbud]          # valgfri – produkter linket herfra hentes først
  shipping: 59                   # valgfri – fragt i kr., vises på siden
  free_shipping_over: 1000       # valgfri – gratis fragt ved køb over dette beløb
```

Kør derefter `python3 -m vinjagt detect`. Hvis en shop ikke kan scannes (ingen API, intet sitemap eller ingen
schema.org-data), vises det med status på siden *Forhandlere*. Til sådanne shops kan `product_url_pattern` (regex)
hjælpe crawleren med at finde produktsiderne.

## Kvalitetskilder

| Kilde | Hvordan | Troværdighed |
|---|---|---|
| Internationale anmeldere (Parker, Suckling, Decanter, Falstaff, Vinous …) | læses fra produktteksten | vægtes 0,7 – 0,9 når flere forhandlere oplyser det samme |
| Danske medier (Politiken, Berlingske, Børsen, JP …), stjerner | læses fra produktteksten | som ovenfor; 5 stjerner = 90 p |
| Egne CSV-filer i `ratings/` | se `ratings/README.md` | 1,0 |
| Vivino (brugervurderinger + dansk gennemsnitspris) | henter Vivinos explore-liste for DK og matcher lokalt | vægtes efter antal stemmer |

**Om Vivino:** Vivino har ingen officiel offentlig API, og deres vilkår begrænser automatiseret adgang. Kilden er
derfor slået fra (`ratings.vivino.enabled: false`) og henter, hvis du slår den til, kun et begrænset antal sider
én gang om ugen. Det er dit valg, om du vil bruge den. Wine-Searcher har en betalt API, som kan tilføjes som en ny
kilde i `vinjagt/ratings/` efter samme mønster.

## God opførsel

Scanneren identificerer sig med `user_agent` (skriv din kontakt-e-mail), følger `robots.txt` inkl. `Crawl-delay`,
holder mindst 2 sekunders pause mellem kald til samme shop, bruger offentlige produkt-API'er/sitemaps hvor muligt
og kører om natten. Data bruges til privat prissammenligning med link tilbage til forhandleren.

## Udvikling

```bash
pip install -r requirements.txt pytest
python -m pytest            # enhedstests + ende-til-ende-test mod tre falske shops
python tests/fake_shops.py  # start falske shops på port 8701-8703 til manuel afprøvning
```

Struktur:

```
vinjagt/
  platforms/   shopify.py, woocommerce.py, generic.py (sitemap + JSON-LD)
  ratings/     vivino.py, csvfile.py
  extract.py   priser, mængderabat, førpris, årgang, størrelse, vintype, land, anmelderpoint
  matching.py  genkendelse af samme vin på tværs af shops
  scoring.py   referencepriser, kvalitet, værdi og samlet score
  site.py      bygger site/ (kopierer web/ og skriver data/*.json)
  publish.py   skubber site/ til git-grenen
web/           HTML/CSS/JS til hjemmesiden (ingen build-trin, ingen afhængigheder)
deploy/        install.sh til Raspberry Pi (systemd-timer og webserver)
```

## Licens

MIT – se [LICENSE](LICENSE).
