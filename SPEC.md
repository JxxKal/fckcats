# Spezifikation — fckcats

Webapp als Docker-Compose-Stack. Nimmt Zeitnachweis-PDFs entgegen, validiert die
geleisteten Arbeitsstunden und erzeugt daraus eine SAP-CATS-Mass-Upload-XLSX, in
der die Stunden auf die WBS-Elemente des Users verteilt sind.

> Alle Beispieldaten in diesem Dokument sind synthetisch.
>
> Die Endpunkte sind in [docs/API.md](docs/API.md) beschrieben, die Einrichtung in
> [DEPLOYMENT.md](DEPLOYMENT.md).

---

## 1. Fundament

Das Basement (SAML, TLS, Hostname) ist aus einem bestehenden Projekt übernommen
und hier auf den Bedarf reduziert.

| Baustein | Anpassung |
|---|---|
| SAML 2.0 SP (Login, ACS, SP-Metadata, SLO) | IdP-Config in `system_config`, Attribut-Mapping konfigurierbar |
| TLS-Verwaltung (Upload PEM + PFX, Self-Signed, ACME-Konfig) | unverändert |
| Hostname → nginx `server_name`, 443-Listener, HTTP→HTTPS-Redirect | unverändert |
| JWT-Ausgabe, `require_admin`-Dependency | unverändert |
| Frontend-Gerüst (React 18 + Vite + Tailwind) | Seiten neu |

### Stack

```
nginx (80/443)  →  api (FastAPI)  →  postgres
   │                    │
   └─ React SPA         ├─ Volume /data   PDFs + generierte XLSX
                        └─ Volume /certs  cert.pem, key.pem, .hostname, .mode
```

### Auth

- **Identität = SAML-`username`** (Attribut-Mapping konfigurierbar, Default `uid`,
  Fallback NameID). Dieser Wert ist der Workspace-Schlüssel; jeder User sieht
  ausschließlich eigene Daten.
- **Lokaler Admin-Fallback** (`source='local'`). Ohne ihn gäbe es ein
  Henne-Ei-Problem: SAML lässt sich erst konfigurieren, wenn man eingeloggt ist.
  Erster Start legt einen Admin an, Passwort aus `.env`, Wechsel bei Erstlogin erzwungen.
- Rollen: `admin` (TLS, Hostname, SAML, Benutzer) und `user` (nur eigener Workspace).
- **Benutzerverwaltung** in den Einstellungen: lokale Konten anlegen, Rolle und Zugang
  ändern, Passwort setzen. SAML-Konten erscheinen nach der ersten Anmeldung automatisch
  in derselben Liste; ihr Kennwort verwaltet der Identity Provider, hier lassen sich
  nur Rolle und Zugang steuern. Der letzte aktive Administrator kann weder herabgestuft
  noch deaktiviert werden.

---

## 2. PDF-Import

### Erkanntes Format

Extraktion per `pdftotext -layout` (kein OCR nötig). Relevanter Block:

```
Tag        von     bis     Std.    Sollz   Verfall  GLZ    Mehrz   Prod
01 MI      08:00   16:30    8,50    7,50             0,50   0,00    8,00
04 SA  Frei laut AZP
17 FR  Urlaub     08:30   16:00    7,50    7,50             0,00   0,00    0,00
```

- **Kopf:** `00123456 Erika Mustermann` → Personalnummer + Name (Abgleich gegen Config).
- **`Monat: Juli - 2026`** → liefert Monat und Jahr für die Tagesnummern.
- **Grund-Feld:** freier Text zwischen Tagesnummer und `von`.
- Dezimaltrennzeichen ist das Komma, negative Werte haben ein **nachgestelltes** Minus (`0,94-`).

### Buchungsbasis: Spalte `Prod.`

`Std.` ist die Brutto-Anwesenheit **inklusive Pause**, `Prod.` die Nettoarbeitszeit.
Die Differenz ist der gesetzliche Pausenabzug (30 min, ab 9 h Arbeitszeit 45 min):

```
01 MI   08:00–16:30   Std. 8,50   Prod. 8,00    → 0,50 Pause
08 MI   06:45–18:15   Std. 11,50  Prod. 10,75   → 0,75 Pause
```

Nur die Summe der `Prod.`-Spalte trifft die im Zeitnachweis ausgewiesenen
„Produktivstunden Monat" exakt. Buchungsbasis ist daher `Prod.`.

### Ausschlussliste

Ein Tag ist **nicht** buchbar, wenn das Grund-Feld einem dieser Muster entspricht
(case-insensitiv, `*` = Präfix-Match):

```
Frei laut AZP · Urlaub · AU · AU ohne Attest · AU mit Attest
Arbeitsunfähig* · Reisezeit · Sonderurlaub · Zeitausgleich*
```

Kritisch: Bei `Urlaub` und `AU …` steht trotzdem `7,50` in Std./Sollz. Ohne diese
Liste würden Abwesenheitstage als Projektzeit gebucht.

### Unbekannte Gründe → Klärfall

Steht im Grund-Feld ein Text, der weder leer noch auf der Ausschlussliste ist
(z. B. `Dienstreise`, `Fortbildung`, `Feiertag`, `Betriebsversammlung`), wird der
Tag als **Klärfall** markiert. Der User entscheidet einmalig „buchen" oder
„ausschließen"; die Entscheidung wird pro User gespeichert und künftig automatisch
angewendet (einsehbar und änderbar in der Config).

### Alles ist von Hand anpassbar

Die Vorschau ist keine reine Anzeige: Jede Zeile lässt sich bearbeiten — Stunden
ändern, Tage ab- oder zuwählen, im PDF fehlende Tage ergänzen. Eine Anpassung hat
immer Vorrang vor der automatischen Einordnung.

- Ein abgewählter Tag wird beim Übernehmen **entfernt**, falls aus einem früheren
  Import noch etwas dasteht. Dasselbe gilt für Tage, die das PDF inzwischen als
  Urlaub oder frei ausweist.
- Von Hand gesetzte Stunden werden als `manual` gekennzeichnet.
- Werte unter der Mindestbuchung von 0,5 h werden abgelehnt.
- Klärfälle bleiben offen, bis sie angefasst wurden — auch wenn die Voreinstellung
  „nicht buchen" lautet. Sonst fiele ein Tag mit vergessener Buchung stillschweigend
  unter den Tisch.

### Fehlende Buchungen → Klärfall

Ein Werktag ist ein Klärfall, wenn **kein** Grund eingetragen ist **und** eines gilt:

- `Prod.` fehlt oder ist 0
- `von` oder `bis` fehlt (Kommen/Gehen vergessen)

Der User bekommt pro Fall die Wahl: **Sollzeit übernehmen**, **eigenen Wert
eintragen** oder **Tag verwerfen**. Manuell ergänzte Stunden werden als `manual`
markiert und im UI ausgewiesen.

### Ergebnis: validierte Tagesliste

`(user, datum) → stunden, quelle {pdf|manual}, grund_text, pdf_id`

`(user, datum)` ist eindeutig. Ein erneuter Import desselben Zeitraums überschreibt
(siehe §5). Überlappende PDFs sind damit unkritisch.

---

## 3. CATS-Config (pro User, dauerhaft)

- **Personalnummer** — z. B. `00123456`. Wird beim PDF-Import gegen den Kopf geprüft;
  Abweichung → Warnung.
- **Nicht gebuchte Zeit** — Stunden je Woche, die keinem WBS-Element zugeordnet und
  nicht exportiert werden, etwa Verwaltungstätigkeit. Sie werden vor allem anderen
  abgezogen.

Der Arbeitsvorrat besteht aus zwei Gruppen:

- **Projekte** — WBS-Element + Obergrenze in Stunden je Woche. Werden zuerst bedient.
- **Operations oder andere gewichtet verteilte WBS-Elemente** — WBS-Element +
  Gewichtung in %. Teilen unter sich auf, was die Projekte übrig lassen.

Dazu:

- **Vorrang bei Knappheit** — `projects` oder `operations`, mit einem
  **Mindestanteil für Operations** in Prozent. Greift nur, wenn die
  Projekt-Obergrenzen mehr ergeben, als die Woche hergibt.
- Schema-Validierung gegen die bekannten Formen:
  `DEO1111-NP/PJ00-O51.0000` (mit Bindestrich-Segment) und
  `DEO5555000/PQ00-A02.0000` (ohne). Freitext bleibt erlaubt, unbekannte Formen
  werden markiert.
- **Die Summe der Gewichtungen muss exakt 100 %** sein — Speichern wird sonst
  abgelehnt. Sie beziehen sich auf den Rest nach den Projekten, nicht auf die
  ganze Woche.
- Ein WBS-Element gehört entweder zu den Projekten oder zu Operations, nie zu beidem.
- Mindestens ein Element insgesamt ist erforderlich; eine Konfiguration nur aus
  Projekten oder nur aus gewichteten Elementen ist zulässig.
- **Plausibilitätswarnung beim Speichern:** Die App rechnet gegen eine typische
  Arbeitswoche (Default 38 h) vor, welche Gewichtungen unter die Mindestbuchung von
  1 h fallen, und nennt die betroffenen Elemente samt nötiger Mindestgewichtung.
  Bei 37 h Wochenarbeitszeit ist ein Element mit 2 % (0,74 h) nicht sinnvoll
  buchbar; ab ca. 3 % geht es auf.
- **Entscheidungen zu unbekannten Grund-Texten** (siehe §2) — Liste, editierbar.
- Config ist **versioniert**; jede Berechnung merkt sich die verwendete Version.

---

## 4. Verteilalgorithmus

**Zwei Regeln mit klarer Rangfolge:**

1. **Hart:** Die Tagessumme der erzeugten Zeilen entspricht exakt der validierten
   Stundenzahl des Tages. Wird nie gerundet, gekürzt oder geschummelt.
2. **Weich:** Die Gewichtung soll je ISO-Woche (Mo–So) möglichst gut getroffen
   werden. Exakt geht nicht, weil die Tagesstunden krumm sind.

**Slice-Präferenz:** möglichst grobe Blöcke — Leiter `4 h → 2 h → 1 h`, der krumme
Rest des Tages landet in einem Slice (`4,00 + 2,00 + 0,82`).

**Mindestbuchung 0,5 h.** CATS nimmt kleinere Zeiten nicht an, also darf keine Zeile
darunter liegen — auch nicht der krumme Tagesrest. Aus 8,05 h wird deshalb
`4,00 + 2,00 + 1,05 + 1,00` statt einer Zeile mit 0,05 h. Ebenso entfallen
Projektanteile und ungebuchte Zeit, die anteilig unter 0,5 h fielen; ihre Stunden
gehen dann in die gewichtete Verteilung. Die Zuordnung wird dadurch etwas gröber,
dafür ist die Datei einspielbar.

### Wochenziele: erst abziehen, dann verteilen

Vor der Verteilung wird je Woche ausgerechnet, wie viele Stunden jedes WBS-Element
bekommen soll:

0. **Nicht gebuchte Zeit abziehen.** Was der Benutzer als ungebucht angegeben hat,
   wird anteilig zur Wochenlänge gekürzt und steht der Verteilung nicht mehr zur
   Verfügung — weder den Projekten noch der Gewichtung.
1. **Projekte zuerst.** Die Obergrenze wird anteilig zur Wochenlänge gekürzt:
   `Obergrenze × Arbeitstage / 5`. Eine Randwoche mit zwei Tagen trägt also 40 % des
   Projektblocks — sonst würde ein 10-h-Projekt eine 12-h-Woche fast allein füllen.
2. **Deckel.** Bei Vorrang für Operations dürfen die Projekte höchstens
   `100 % − Mindestanteil` der Woche belegen. Bei Vorrang für die Projekte gibt es
   keinen Deckel.
3. **Überzeichnung.** Übersteigen die Projekte den Deckel, werden sie anteilig im
   Verhältnis ihrer Obergrenzen gekürzt.
4. **Rest an Operations.** Was übrig bleibt, verteilt sich nach den Gewichtungen —
   plus dem Übertrag aus der Vorwoche.

Der Übertrag gilt **nur für Operations**. Bei Projekten ist die Angabe eine
Obergrenze und kein Soll: eine Woche, in der weniger anfiel, darf die nächste nicht
aufblähen.

### Nicht gebuchte Zeit

Technisch läuft sie als Platzhalter durch dieselbe Verteilung wie ein Projekt: sie
bindet Stunden an einzelnen Tagen, fällt aber vor dem Erzeugen der Zeilen wieder
heraus. Dadurch bleibt die Blockstruktur der übrigen Buchungen erhalten.

**Folge für die XLSX:** Sie enthält dann weniger Stunden als der Zeitnachweis. Die
Tagessumme entspricht exakt der erfassten Zeit **abzüglich** der ungebuchten — was
gebucht wird, stimmt weiterhin auf die Stunde.

Weil die Verteilung in groben Blöcken arbeitet, ist die tatsächlich ungebuchte
Stundenzahl ein Richtwert: bei 4 h je Woche und einer Woche mit nur einem Arbeitstag
blieben statt der rechnerischen 0,80 h etwa 0,55 h ungebucht. Über den Zeitraum
gleicht sich das aus.

Rechenbeispiel bei 38 h Wochenarbeitszeit, Projekte 10 h und 6 h, Operations 60/40:

| Fall | Projekte | Operations |
|---|---|---|
| Volle Woche | 10,00 + 6,00 h | 13,20 + 8,80 h |
| Randwoche, 2 von 5 Tagen (12,68 h) | 4,00 h | 5,21 + 3,47 h |
| Überzeichnet (3 × 15 h), Vorrang Projekte | 12,67 h je Projekt | 0 h |
| Überzeichnet, Vorrang Operations mit 30 % | 8,87 h je Projekt | 6,84 + 4,56 h |

### Ablauf je Woche

1. Wochenziele je WBS-Element wie oben berechnet.
2. Tage in zufälliger Reihenfolge durchgehen. Je Tag, solange Restzeit > 0:
   - Element mit dem größten offenen Restbedarf wählen (Gleichstand zufällig).
   - Größten Block der Leiter nehmen, der in die Restzeit passt und den Bedarf
     nicht stark überschießt.
   - Würde ein Krümel < 1 h übrigbleiben, der nicht dem krummen Tagesanteil
     entspricht, nimmt der aktuelle Slice den ganzen Rest.
   - Ein WBS-Element höchstens **einmal pro Tag**, höchstens **4 Slices pro Tag**;
     der letzte Slice nimmt garantiert den Rest → Tagessumme stimmt
     konstruktionsbedingt.
3. Restabweichung der Operations-Elemente geht als **Übertrag in die Folgewoche**.
   Damit gleichen sich Randwochen über den Zeitraum wieder aus.

### Auswahl der besten Variante

Die Verteilung ist zufällig, entsprechend schwankt ihre Qualität je nach Seed
erheblich. Deshalb rechnet die App standardmäßig 200 Varianten durch und nimmt
die wochenscharf beste — bewertet über die Summe der quadrierten
Wochenabweichungen, damit eine unvermeidbar grobe Randwoche nicht die
Optimierung der übrigen Wochen blockiert. Das kostet bei einem Monat wenige
hundert Millisekunden, bei einem halben Jahr unter einer Sekunde. Der
Gewinner-Seed wird gespeichert und reproduziert das Ergebnis exakt.

### Verifiziert

Implementierung in `api/src/distribution.py`, Tests in `api/tests/`. Ergebnis
bei Gewichtung 40/25/15/15/5 über einen Zeitraum mit zwei Randwochen (2 Tage, 5 Tage, 1 Tag):

| Soll | KW10 (2 Tage) | KW11 (5 Tage) | KW12 (1 Tag) | **Gesamt** |
|---|---|---|---|---|
| 40 % | 39,4 % | 37,7 % | 53,0 % | **40,1 %** |
| 25 % | 23,7 % | 23,9 % | 26,5 % | **24,2 %** |
| 15 % | 21,1 % | 17,6 % | 0,0 % | **16,0 %** |
| 15 % | 15,8 % | 15,4 % | 13,2 % | **15,2 %** |
| 5 % | 0,0 % | 5,4 % | 7,3 % | **4,4 %** |

Alle Tagessummen exakt, Gesamtsumme exakt. In der vollen Woche liegt die Abweichung
unter 2,6 Prozentpunkten; Randwochen mit 1–2 Tagen sind naturgemäß grob, werden aber
über den Übertrag eingefangen.

**Teilwochen** (Monatsanfang/-ende, Datenrand) werden „best effort" verteilt und
sofort exportierbar — nicht zurückgehalten. Die Abweichung wird im UI je Woche
ausgewiesen.

**Reproduzierbarkeit:** Der Zufallsseed wird je Berechnungslauf persistiert. Eine
einmal berechnete Verteilung ist eingefroren; ein erneuter Export desselben
Zeitraums liefert identische Zeilen.

---

## 5. Zieltabelle, Historie, Export

### Persistente Zieltabelle im Workspace

`(user, datum, wbs_element) → stunden, berechnungslauf, exported_at, export_id`

Sie ist der eigentliche Bestand, nicht die XLSX. Die XLSX ist nur eine Sicht darauf.

**Die Zieltabelle zeigt den offenen Bestand.** Eine exportierte Zeile ist gebucht und
damit erledigt; sie verschwindet aus der Anzeige. Sonst mischte sich der Inhalt eines
abgeschlossenen Imports mit dem eines neuen und die Wochenansicht wäre nicht mehr zu
lesen. Nach einem Export über den ganzen Bestand ist die Zieltabelle leer. Daraus
folgt:

- Ausgeblendet wird nicht nur die Zeile, sondern auch die erfasste Zeit, die zu ihr
  gehört — sonst taucht die exportierte Stunde als „nicht gebucht" wieder auf.
  Gerechnet wird das je **Tag**, damit eine halb exportierte Woche stimmt: ein Tag,
  dessen Zeilen alle exportiert sind, fällt heraus; ein Tag mit exportierten *und*
  offenen Zeilen bringt nur den Rest seiner erfassten Stunden mit.
- Ein Tag ohne jede Zeile bleibt stehen: er ist nicht abgearbeitet, sondern besteht
  ausschließlich aus nicht gebuchter Zeit.
- Eine Woche, von der danach nichts übrig bleibt, verschwindet. Bei einer
  angebrochenen Woche nennt die Kopfzeile die Zahl der ausgeblendeten Zeilen, damit
  es nicht so aussieht, als fehle die halbe Woche.

Gelöscht wird nichts: *exportierte Zeilen einblenden* zeigt den vollen Bestand,
die Export-Historie sowieso, und die Rücknahme eines Exports holt die Zeilen zurück
in den offenen Bestand.

**Buchungsstatus und Export-Verweis sind bewusst getrennt.** `exported_at` sagt, ob
eine Zeile bereits nach SAP gegangen ist; `export_id` verweist auf den Datensatz des
Exports. Hinge der Status am Fremdschlüssel, würde das Aufräumen der Export-Historie
alle Zeilen wieder auf „offen" setzen — sie kämen in den nächsten Export und wären in
SAP doppelt gebucht. Ein gelöschter Export nullt daher nur den Verweis.

### Export

- User wählt einen Zeitraum → XLSX wird erzeugt und heruntergeladen.
- Enthaltene Zeilen werden als `exportiert` markiert, mit Zeitstempel und Datei-ID.
- Die erzeugte Datei bleibt im Workspace abrufbar (Historie, erneuter Download).
- Default-Vorauswahl beim Export: alle Zeilen mit Status `offen`.

### Historie aufräumen

- **Export zurücknehmen** — die Zeilen gelten wieder als offen und werden erneut
  ausgegeben. Für den Fall, dass die Datei nie in SAP angekommen ist. Eintrag und
  Datei bleiben.
- **Export löschen** — Eintrag und Datei verschwinden, der Buchungsstatus bleibt.
  Reines Aufräumen, ohne Folgen für den nächsten Export.
- **Gesamte Export-Historie löschen** — wie oben für alle Einträge, wahlweise mit
  ausdrücklicher Freigabe aller Zeilen (`revoke=true`). Nur dieser eine Weg macht
  bereits gebuchte Zeiten wieder exportierbar.
- **Änderungsprotokoll löschen** — leert das Protokoll ersetzter Zeilen. Zieltabelle,
  Buchungsstatus und Export-Historie bleiben unberührt.

Alle Löschvorgänge verlangen `confirm=true`.

### Re-Import eines korrigierten PDFs

Betroffene Tage werden neu berechnet. Sind darunter bereits **exportierte** Zeilen,
zeigt die App vor dem Überschreiben eine Diff-Ansicht (alt/neu je Tag) und verlangt
eine ausdrückliche Bestätigung. Der Ersatz wird protokolliert; die alte Fassung
bleibt einsehbar.

---

## 6. Datenspeicherung und Verschlüsselung

### Speichermodus je Benutzer

Jeder Benutzer entscheidet selbst, ob überhaupt etwas aufbewahrt wird.

| | `persistent` | `ephemeral` |
|---|---|---|
| Arbeitszeiten, Zieltabelle, PDFs, Exporte, Historie | gespeichert (verschlüsselt) | nichts davon |
| Warnung vor Doppelbuchung | ja | nein |
| Abgleich eines korrigierten PDFs gegen den Bestand | ja | nein |
| CATS-Config | gespeichert (verschlüsselt) | gespeichert (verschlüsselt) |

Im Modus `ephemeral` läuft der Ablauf in einem Zug: PDF hochladen → Vorschau und
Klärfälle → `POST /api/exports/direct` verteilt und liefert die XLSX zurück. Das PDF
liegt dabei nur für die Dauer des Aufrufs in einem temporären Verzeichnis. Was bereits
nach CATS gebucht wurde, muss der Benutzer selbst im Blick behalten.

Die Config bleibt in beiden Fällen erhalten — ohne Personalnummer und WBS-Vorrat wäre
jeder Durchgang eine Neuerfassung. Beim Wechsel nach `ephemeral` wird alles bereits
Gespeicherte gelöscht; eine Zustimmung zurückzunehmen, die das Vorhandene stehen ließe,
wäre wirkungslos.

### Schlüsselhierarchie

Jeder Benutzer hat einen eigenen Datenschlüssel (DEK, 32 Byte). Der DEK liegt nur
eingewickelt in der Datenbank:

- **`master`** — eingewickelt mit einem Schlüssel, den HKDF aus `DATA_MASTER_KEY` und
  einem benutzereigenen Salt ableitet. Standard, ohne Zutun des Benutzers.
- **`passphrase`** — eingewickelt mit einem Schlüssel, den scrypt aus einer Passphrase
  ableitet, die nur der Benutzer kennt. Der Client hält den ausgewickelten DEK für die
  Dauer der Sitzung und schickt ihn im Kopfzeilenfeld `X-Data-Key` mit.

Ein Wechsel der Passphrase wickelt denselben DEK neu ein — die Daten müssen dabei nicht
angefasst werden.

### Verschlüsselt wird

AES-256-GCM, Nonce je Vorgang zufällig. Betroffen sind alle Nutzdaten: Stunden,
WBS-Elemente, Personalnummer, Gewichtungen, Grund-Entscheidungen, das Auswertungs-
ergebnis der PDFs, die hochgeladenen PDFs selbst und die erzeugten XLSX-Dateien.

### Klartext bleibt

Fremdschlüssel, Datumsangaben, Zeitstempel, der Buchungsstatus — sie werden zum Filtern
gebraucht. Ebenso die Konto-Stammdaten (Benutzername, Anzeigename, E-Mail), da die
Anmeldung sie nachschlagen muss.

Ein Angreifer mit Datenbankzugriff sieht also, **an welchen Tagen** jemand gearbeitet
und **wann** er exportiert hat — aber weder Stundenzahl noch WBS-Element,
Personalnummer oder PDF-Inhalt.

Weil `wbs_element` verschlüsselt ist, trägt ein HMAC über den Wert
(`cats_entry.wbs_hash`) die Eindeutigkeit je Tag. Ein einfacher Hash würde nicht
genügen — WBS-Elemente haben zu wenig Entropie, um einem Wörterbuchangriff
standzuhalten.

### Wogegen das schützt — und wogegen nicht

**Geschützt:** kopierte Volumes, Datenbank-Dumps, Backups, ausgebaute Platten. Im Modus
`master` reicht dem Betreiber allerdings die `.env`; erst eine Passphrase sperrt ihn aus.

**Nicht geschützt:** Zugriff auf den laufenden Prozess. Während einer Sitzung liegt der
DEK im Arbeitsspeicher, weil PDF-Auswertung und Verteilung auf dem Server stattfinden.
Auch mit Passphrase ist das **kein** Ende-zu-Ende-Schutz — dafür müsste die gesamte
Verarbeitung im Browser laufen.

**Geht die Passphrase verloren, sind die Daten verloren.** Es gibt kein Zurücksetzen;
das ist der Preis dafür, dass der Betreiber sie nicht öffnen kann.

---

## 7. XLSX-Zielformat

Exakt nach dem CATS-Mass-Upload-Muster:

- **Ein Sheet**, Spalten A–J:
  `WORKDATE | EMPLOYEE | WBS_ELEMENT | ABS_ATT_TYPE | REC_ORDER | ACTIVITY | WAGETYPE | CATSHOURS | ZZICTPC | SHORTTEXT`
- **Die Kopfzeile steht dreimal** (Zeile 1, 2, 3). Daten ab **Zeile 4**.
- `WORKDATE` — echtes Excel-Datum, Zahlenformat `numFmtId=14`.
- `EMPLOYEE` — Personalnummer als **Zahl ohne führende Nullen** (`00123456` → `123456`).
- `WBS_ELEMENT` — Text, unverändert aus der Config.
- `CATSHOURS` — Zahl, 2 Nachkommastellen.
- `ABS_ATT_TYPE`, `REC_ORDER`, `ACTIVITY`, `WAGETYPE`, `ZZICTPC`, `SHORTTEXT` — **bleiben leer**.

Eine Tageszeile der validierten Liste wird zu 1–4 XLSX-Zeilen, je nach Anzahl der
zugeteilten WBS-Elemente.

### Was SAP an der Datei prüft

Die Datei wird von Hand geschrieben (`api/src/xlsx_export.py`), nicht über eine
Bibliothek. Zwei Eigenschaften entscheiden darüber, ob SAP sie überhaupt annimmt:

- **Texte gehören in die Shared-String-Tabelle** (`xl/sharedStrings.xml`), die Zelle
  verweist nur darauf (`<c t="s"><v>7</v></c>`). openpyxl schrieb sie stattdessen als
  *inline strings* in die Zelle selbst. Für Excel ist beides gültig, SAP liest nur die
  Tabelle — das WBS-Element, die einzige Textspalte der Datenzeilen, kam leer an und
  der Upload scheiterte. Wer die Datei vorher einmal in Excel öffnete und speicherte,
  bekam die Tabelle geschenkt; genau das verdeckte die Ursache und ließ die Schuld
  bei den Stundenwerten suchen.
- **`[Content_Types].xml` muss der erste Eintrag im ZIP sein.** OPC verlangt es;
  openpyxl hängte ihn ans Ende.

Beides ist mit einem Test abgesichert. Die erzeugten Bytes sind reproduzierbar:
derselbe Export ergibt dieselbe Datei.

---

## 8. UI-Fluss

```
Login (SAML oder lokal)
  └─ Dashboard: offene Stunden, letzter Export, Warnungen
  └─ CATS-Config: Personalnummer, nicht gebuchte Zeit, Projekte mit
       Wochen-Obergrenze, gewichtete Elemente, Vorrang bei Knappheit,
       Grund-Entscheidungen
  └─ Import: PDF hochladen
       └─ Vorschau: erkannte Tage, ausgeschlossene Tage mit Grund
       └─ Klärfälle: unbekannte Gründe + fehlende Buchungen abarbeiten
       └─ Übernehmen
  └─ Zieltabelle: offener Bestand, nach Woche gruppiert, Soll/Ist-Gewichtung
       je Woche, Zeitraum wählen → XLSX; exportierte Zeilen auf Wunsch einblenden
  └─ Historie: erzeugte Exporte (Download, zurücknehmen, löschen)
       └─ Änderungsprotokoll ersetzter Zeilen, löschbar
  └─ Datenschutz: Speicherung ein/aus, Bestand einsehen und löschen,
       eigene Passphrase setzen, ändern, entfernen
  └─ [admin] Einstellungen
       └─ Benutzer: lokale anlegen, Rolle, Zugang, Passwort setzen;
            SAML-Benutzer erscheinen nach ihrer ersten Anmeldung
       └─ Hostname, TLS-Zertifikat
       └─ SAML: IdP-Metadata einlesen (Datei oder eingefügt), Felder,
            SP-Metadata zum Herunterladen
```

---

## 9. Gesetzte Annahmen

1. Lokaler Admin-Fallback neben SAML (nötig wegen Henne-Ei bei der SAML-Konfiguration).
2. Woche = ISO-Woche Mo–So.
3. Höchstens 4 Slices pro Tag, jedes WBS-Element höchstens einmal pro Tag.
4. Übertrag der Wochenabweichung in die Folgewoche ist aktiv.
5. PDFs und erzeugte XLSX werden dauerhaft im Workspace aufbewahrt.
6. WBS-Elemente haben keine Gültigkeitszeiträume.
7. Speicher: Postgres + Docker-Volume. Kein Objektspeicher.
8. Konto-Stammdaten bleiben im Klartext, weil die Anmeldung sie nachschlagen muss.
9. Der Speichermodus gilt je Benutzer, nicht systemweit.
