# Egne vurderingskilder (CSV)

Læg CSV-filer her, så indlæses de ved hver kørsel og matches mod vinene fra forhandlerne.

Kolonner (`;` eller `,` som separator):

| kolonne  | betydning                                               |
|----------|---------------------------------------------------------|
| source   | kildens navn, fx `Politiken` (ellers filnavnet)         |
| name     | vinens navn                                             |
| producer | producent (valgfri, men forbedrer matchet)              |
| vintage  | årgang (tom = alle årgange)                             |
| rating   | vurderingen                                             |
| scale    | `100` (point), `20` (Jancis), `6` (stjerner), `5`       |
| count    | antal stemmer (valgfri)                                 |
| price    | referencepris i kr. (valgfri)                           |
| url      | link til anmeldelsen (valgfri)                          |

Se `eksempel.csv.txt` – omdøb til `.csv` for at bruge den.
