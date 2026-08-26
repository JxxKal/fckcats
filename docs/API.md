# API-Referenz

Alle Endpunkte liegen unter `/api/`. Die interaktive Fassung erzeugt FastAPI selbst:

- **Swagger UI** — `https://<host>/api/docs`
- **ReDoc** — `https://<host>/api/redoc`
- **OpenAPI-Schema** — `https://<host>/api/openapi.json`

## Authentifizierung

Nach der Anmeldung liefert die API ein JWT (8 Stunden gültig), das als
`Authorization: Bearer <token>` mitgeschickt wird.

Hat ein Benutzer eine eigene Passphrase gesetzt, kommt ein zweites Kopfzeilenfeld
dazu: `X-Data-Key: <hex>`. Der Wert stammt aus `POST /api/privacy/unlock` und wird
serverseitig nicht aufbewahrt. Fehlt er, antworten alle Endpunkte, die Nutzdaten
berühren, mit **412**.

```bash
TOKEN=$(curl -s -X POST https://fckcats.example.org/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"…"}' | jq -r .token)

curl -s https://fckcats.example.org/api/entries -H "Authorization: Bearer $TOKEN"
```

## Anmeldung

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/api/auth/login` | Lokale Anmeldung, liefert JWT |
| `GET` | `/api/auth/me` | Angemeldeten Benutzer lesen |
| `POST` | `/api/auth/password` | Eigenes Passwort ändern |

### SAML

| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/api/auth/saml/enabled` | Ist SAML aktiv? Wird vom Anmeldebildschirm abgefragt |
| `GET` | `/api/auth/saml/login` | SP-initiierte Anmeldung, leitet zum IdP weiter |
| `POST` | `/api/auth/saml/acs` | Assertion Consumer Service, nimmt die Antwort des IdP entgegen |
| `GET` | `/api/auth/saml/metadata` | SP-Metadata zum Eintragen beim IdP |
| `GET`/`POST` | `/api/auth/saml/sls` | Single Logout |

Nach erfolgreicher Anmeldung leitet der ACS auf `/?saml_token=<jwt>` weiter; die
Oberfläche übernimmt das Token und entfernt es aus der Adresszeile.

## CATS-Config

| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/api/cats-config` | Aktuelle Fassung lesen |
| `PUT` | `/api/cats-config` | Speichern — legt eine neue Version an |

Die Summe der Gewichtungen muss exakt 100 % ergeben, sonst **400**. Die Antwort
enthält `warnings` mit Elementen, deren Gewichtung unter der Mindestbuchung liegt.

```json
{
  "personnel_number": "00123456",
  "projects": [
    {"wbs": "PRJ-4711/PJ00-O51.0000", "max_hours_per_week": 10},
    {"wbs": "PRJ-4712/PJ00-O51.0000", "max_hours_per_week": 6}
  ],
  "wbs_elements": [
    {"wbs": "DEO1111-NP/PJ00-O51.0000", "weight": 60},
    {"wbs": "DEO2222-NP/PJ00-O51.0000", "weight": 40}
  ],
  "priority": "projects",
  "operations_min_pct": 0,
  "unbooked_hours_per_week": 0,
  "reason_rules": {"Dienstreise": "book"}
}
```

`unbooked_hours_per_week` sind Stunden je Woche, die keinem WBS-Element zugeordnet
und nicht exportiert werden. Sie werden vor Projekten und Gewichtung abgezogen und
in kürzeren Wochen anteilig gekürzt. Liegt der Wert bei oder über der typischen
Wochenarbeitszeit, antwortet der Endpunkt mit **400**.

`projects` tragen eine Obergrenze in Stunden je Woche und werden zuerst bedient;
`wbs_elements` teilen sich nach Gewicht, was übrig bleibt. `priority` entscheidet bei
Überzeichnung, wer nachgibt — bei `operations` sichert `operations_min_pct` den
gewichteten Elementen einen Mindestanteil der Woche. Ein WBS-Element darf nur in
einer der beiden Listen stehen, sonst **400**.

## Import

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/api/imports` | PDF hochladen und auswerten (multipart, Feld `file`) |
| `GET` | `/api/imports` | Bisherige Uploads auflisten |
| `POST` | `/api/imports/{id}/commit` | Import übernehmen |

Der Upload speichert noch nichts in der Zieltabelle, sondern liefert die Auswertung
zur Ansicht: jeden Tag mit Status, die Summe der buchbaren Stunden, offene Klärfälle
und Hinweise. Im Modus `ephemeral` ist `upload_id` **null** und es wird nichts abgelegt.

`commit` verlangt zu jedem Klärfall eine Entscheidung, sonst **409** mit
`unresolved_dates`. Berührt der Import Tage, die schon exportiert wurden, antwortet er
ebenfalls mit **409** und `exported_dates`; erst `confirm_overwrite_exported: true`
lässt ihn durch.

```json
{
  "adjustments": [
    {"work_date": "2026-03-02", "action": "book", "hours": 6.5},
    {"work_date": "2026-03-05", "action": "book", "hours": null,
     "remember_reason": "Dienstreise"},
    {"work_date": "2026-03-06", "action": "exclude"},
    {"work_date": "2026-03-07", "action": "book", "hours": 4.0}
  ],
  "confirm_overwrite_exported": false
}
```

`adjustments` gelten für **jeden** Tag, nicht nur für Klärfälle — die Vorschau ist
vollständig bearbeitbar. Ohne Eintrag greift die automatische Einordnung aus dem PDF.
Das ältere Feld `clarifications` wird weiterhin akzeptiert und mit `adjustments`
zusammengeführt.

- `hours: null` übernimmt den Wert aus dem PDF; ein eigener Wert wird als `manual`
  gekennzeichnet und muss mindestens **0,6 h** betragen, sonst **400**.
- `action: "exclude"` entfernt den Tag auch dann, wenn er aus einem früheren Import
  noch gespeichert ist.
- Ein Datum, das im PDF gar nicht vorkommt, wird als zusätzlicher Tag angelegt.
- `remember_reason` merkt die Entscheidung dauerhaft in der Config.

Die Antwort nennt neben `imported_days` auch `removed_days` — die Tage, die dieses
PDF abdeckt, die aber nicht gebucht werden.

## Zieltabelle

| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/api/entries` | Nach ISO-Woche gruppiert, mit Soll/Ist je WBS-Element |

Die Antwort weist neben den gebuchten Stunden (`total_hours`) auch die erfassten
(`recorded_hours`) und die Differenz (`unbooked_hours`) aus, je Woche und in der
Summe.

Voreingestellt ist `only_open=true`: bereits exportierte Zeilen bleiben draußen, die
Zieltabelle zeigt den offenen Bestand. Mit ihnen entfällt die erfasste Zeit, die zu
ihnen gehört — tagesgenau, damit `unbooked_hours` einer halb exportierten Woche
stimmt. Wochen, von denen danach nichts übrig bleibt, tauchen nicht mehr auf.
`hidden_exported_rows` nennt die Zahl der ausgeblendeten Zeilen, je Woche steht sie
in `exported_rows`. Mit `only_open=false` kommt der volle Bestand zurück.

| `GET` | `/api/entries/workdays` | Validierte Tagesliste |
| `POST` | `/api/entries/recalculate` | Offene Zeilen neu verteilen |
| `GET` | `/api/entries/history` | Protokoll ersetzter Zeilen |
| `DELETE` | `/api/entries/history?confirm=true` | Protokoll leeren |

`recalculate` fasst ausschließlich Zeilen an, die noch nicht exportiert wurden.
Mit `?seed=<zahl>` lässt sich ein früherer Lauf reproduzieren.

## Export

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/api/exports?date_from=…&date_to=…` | Offene Zeilen exportieren |
| `POST` | `/api/exports/direct` | XLSX erzeugen, ohne zu speichern |
| `GET` | `/api/exports` | Export-Historie |
| `GET` | `/api/exports/{id}/download` | Datei erneut herunterladen |
| `POST` | `/api/exports/{id}/revoke` | Zurücknehmen — Zeilen gelten wieder als offen |
| `DELETE` | `/api/exports/{id}` | Aus der Historie löschen, Buchungsstatus bleibt |
| `DELETE` | `/api/exports?confirm=true[&revoke=true]` | Gesamte Historie löschen |

**Zurücknehmen und Löschen sind verschiedene Dinge.** Zurücknehmen gibt die Zeilen
wieder frei, sie gehen beim nächsten Export erneut raus. Löschen räumt nur auf; was
einmal gebucht wurde, bleibt gebucht. Nur `revoke=true` beim Löschen der ganzen
Historie tut beides — bei bereits in SAP gebuchten Zeiten führt das zu Doppelbuchungen.

`/api/exports/direct` ist der Weg für Workspaces ohne Speicherung: Der Aufruf bekommt
die bestätigten Tage, verteilt sie und liefert die Datei unmittelbar zurück. Zeilenzahl
und Stundensumme stehen in den Kopfzeilen `X-Row-Count` und `X-Total-Hours`.

```json
{ "days": [{"work_date": "2026-03-02", "hours": 8.0}], "seed": null }
```

## Datenschutz

| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/api/privacy` | Speichermodus, Verschlüsselungsart, Umfang des Bestands |
| `PUT` | `/api/privacy/storage-mode` | Speicherung ein- oder ausschalten |
| `DELETE` | `/api/privacy/data?confirm=true` | Alle Arbeitszeitdaten löschen |
| `POST` | `/api/privacy/passphrase` | Eigene Passphrase setzen |
| `POST` | `/api/privacy/passphrase/change` | Passphrase ändern |
| `DELETE` | `/api/privacy/passphrase` | Passphrase entfernen (verlangt die aktuelle) |
| `POST` | `/api/privacy/unlock` | Workspace entsperren, liefert den Datenschlüssel |

Das Umschalten auf `ephemeral` löscht den vorhandenen Bestand und verlangt deshalb
`confirm_purge: true`, sonst **409**. Die CATS-Config bleibt in jedem Fall erhalten.

Nach `POST /api/privacy/passphrase` öffnet der Master-Schlüssel diesen Workspace nicht
mehr. Der zurückgegebene `data_key` gehört ab dann in jede Anfrage. **Geht die
Passphrase verloren, sind die Daten verloren** — es gibt kein Zurücksetzen.

## Administration

Alle Endpunkte verlangen die Rolle `admin`.

| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/api/admin/users` | Benutzer auflisten, lokale und per SAML angelegte |
| `POST` | `/api/admin/users` | Lokalen Benutzer anlegen |
| `PATCH` | `/api/admin/users/{id}` | Rolle, Zugang, Anzeigename, E-Mail ändern |
| `POST` | `/api/admin/users/{id}/password` | Passwort eines lokalen Kontos setzen |
| `GET` | `/api/admin/saml` | SAML-Einstellungen lesen |
| `PUT` | `/api/admin/saml` | SAML-Einstellungen speichern |

Der letzte aktive Administrator kann weder herabgestuft noch deaktiviert werden.
Ein Passwortversuch auf ein SAML-Konto wird mit **400** abgelehnt — das Kennwort
verwaltet der Identity Provider. Beim Lesen der SAML-Einstellungen erscheint statt des
hinterlegten Zertifikats der Platzhalter `__gespeichert__`; wird er unverändert
zurückgeschickt, bleibt das Zertifikat bestehen.

## TLS

| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/api/ssl/status` | Zertifikatsstatus |
| `POST` | `/api/ssl/upload` | Zertifikat und Schlüssel als PEM |
| `POST` | `/api/ssl/upload-pfx` | PFX/PKCS#12 importieren |
| `POST` | `/api/ssl/self-signed` | Selbstsigniertes Zertifikat erzeugen |
| `POST` | `/api/ssl/acme` | ACME-Konfiguration hinterlegen |
| `GET`/`POST` | `/api/ssl/hostname` | Hostname für `server_name` |

Schreibende Zugriffe verlangen die Rolle `admin`. nginx liest Zertifikat und Hostname
nur beim Start — nach einer Änderung `docker compose restart nginx`.

## System

| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/api/health` | Healthcheck, prüft auch die Datenbank |

## Statuscodes

| Code | Bedeutung |
|---|---|
| `400` | Eingabe unplausibel — etwa Gewichtungen ungleich 100 % |
| `401` | Kein oder abgelaufenes Token, falsches Passwort |
| `403` | Angemeldet, aber nicht berechtigt |
| `404` | Nicht vorhanden, oder nichts zu exportieren im Zeitraum |
| `409` | Bestätigung nötig: offene Klärfälle, bereits exportierte Tage, Löschvorgänge — oder der Datenschlüssel passt nicht |
| `412` | Workspace ist mit einer Passphrase gesperrt |
| `413` | Datei größer als 20 MB |
| `422` | PDF nicht auswertbar |
