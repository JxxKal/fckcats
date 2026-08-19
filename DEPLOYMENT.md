# Deployment Guide

Anleitung für den Betrieb von fckcats als Docker-Compose-Stack.

---

## 1. Voraussetzungen

- Docker Engine 24 oder neuer mit Compose-Plugin v2
  (`docker --version`, `docker compose version`)
- Rund 2 GB freier Plattenplatz für Images, dazu Platz für PDFs und Exporte
- Freie Ports für HTTP und HTTPS (per `.env` einstellbar)
- Für den produktiven Betrieb: ein DNS-Name, der auf den Host zeigt

Der Stack besteht aus drei Diensten:

| Dienst | Aufgabe | Ports |
|---|---|---|
| `nginx` | liefert die Oberfläche aus, terminiert TLS, reicht `/api/` weiter | 80, 443 |
| `api` | FastAPI, PDF-Auswertung, Verteilung, XLSX-Erzeugung | nur intern |
| `postgres` | Datenhaltung | nur intern |

## 2. Installation

```bash
git clone https://github.com/JxxKal/fckcats.git
cd fckcats
cp .env.example .env
```

`.env` bearbeiten. Zwei Werte **müssen** gesetzt werden, der Stack startet sonst nicht:

```bash
SECRET_KEY=$(openssl rand -hex 32)      # signiert die JWTs
DATA_MASTER_KEY=$(openssl rand -hex 32) # verschluesselt die Nutzdaten
BOOTSTRAP_ADMIN_PASSWORD=…              # Startpasswort des lokalen Admins
POSTGRES_PASSWORD=$(openssl rand -hex 16)
```

Praktisch in einem Rutsch:

```bash
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" .env
sed -i "s|^DATA_MASTER_KEY=.*|DATA_MASTER_KEY=$(openssl rand -hex 32)|" .env
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 16)|" .env
```

> **`DATA_MASTER_KEY` ist der Schlüssel zu allen gespeicherten Daten.** Geht er
> verloren, sind Arbeitszeiten, Zieltabellen, PDFs und Exporte unwiederbringlich
> unlesbar — auch aus einem Backup. Er gehört gesichert, aber **getrennt von der
> Datenbanksicherung**: liegen beide am selben Ort, ist die Verschlüsselung wertlos.
> Ist er nicht gesetzt, greift ersatzweise `SECRET_KEY`; dann gilt für den dasselbe.

Ports anpassen, falls 80/443 belegt sind:

```bash
HTTP_PORT=8080
HTTPS_PORT=8443
```

Starten:

```bash
docker compose up -d
```

Der erste Start baut die Images (einige Minuten) und legt danach automatisch das
Datenbankschema sowie den lokalen Administrator an.

## 3. Erste Anmeldung

```bash
docker compose logs api | grep Administrator
```

Die Oberfläche unter `http://<host>:<HTTP_PORT>` öffnen und mit Benutzer `admin` und
dem Passwort aus `BOOTSTRAP_ADMIN_PASSWORD` anmelden. Die App verlangt sofort ein
neues Passwort (mindestens 12 Zeichen).

> Der lokale Zugang existiert, weil sich SAML sonst nie einrichten ließe — die
> Einstellungen setzen einen angemeldeten Administrator voraus.

## 4. Hostname und TLS

Unter **Einstellungen** (nur für Administratoren sichtbar):

**Hostname setzen** — trägt `server_name` in die nginx-Konfiguration ein, z. B.
`fckcats.example.org`. Ohne Eintrag nimmt nginx alle Namen an.

**Zertifikat hinterlegen**, wahlweise:

- *PEM-Upload* — `cert.pem` und `key.pem`, optional die CA-Kette. Die Kette wird an
  das Zertifikat angehängt.
- *PFX-Upload* — PKCS#12 aus einer Windows-CA, mit Passwort. Zertifikat und
  Schlüssel werden extrahiert.
- *Selbstsigniert* — für Tests und interne Umgebungen. Common Name und Laufzeit
  angeben.

Beides landet im Volume `certs`. **nginx liest es nur beim Start**, also danach:

```bash
docker compose restart nginx
docker compose logs nginx | head -3
```

Die Ausgabe zeigt, was greift:

```
[nginx] Hostname: fckcats.example.org
[nginx] Zertifikat gefunden - Port 443 mit Weiterleitung von 80
```

Ohne Zertifikat läuft nginx auf Port 80 ohne TLS. Mit Zertifikat lauscht er auf 443
und leitet 80 dorthin weiter.

### Zertifikat auf der Kommandozeile einspielen

Falls die Oberfläche noch nicht erreichbar ist:

```bash
docker compose cp cert.pem nginx:/certs/cert.pem
docker compose cp key.pem  nginx:/certs/key.pem
echo -n "fckcats.example.org" | docker compose exec -T nginx tee /certs/.hostname
docker compose restart nginx
```

Das Volume `certs` ist in `nginx` schreibgeschützt eingebunden; obige Befehle
funktionieren dennoch, weil `docker compose cp` am Mount vorbei in den Container
schreibt. Sauberer ist der Weg über die Oberfläche oder über den `api`-Container.

## 5. Benutzer verwalten

Unter **Einstellungen → Benutzer**. Die Liste enthält lokale und per SAML angelegte
Konten nebeneinander, mit Anmeldeart, Rolle, Zugang und dem Umfang des jeweiligen
Workspaces.

- **Lokales Konto anlegen** — Benutzername, Startpasswort (mindestens 12 Zeichen),
  optional Anzeigename, E-Mail und Rolle. Das Startpasswort muss beim ersten Login
  geändert werden.
- **Passwort setzen** — nur für lokale Konten. Mit der Vorgabe *Wechsel beim nächsten
  Login erzwingen* kennt danach niemand außer der Person selbst ihr Passwort.
- **Rolle und Zugang** — direkt in der Tabelle. Der letzte aktive Administrator lässt
  sich weder herabstufen noch deaktivieren, damit man sich nicht aussperrt.

SAML-Konten erscheinen automatisch nach der ersten Anmeldung. Ihr Kennwort verwaltet
der Identity Provider; ein Passwortversuch darauf wird abgelehnt.

## 6. Datenspeicherung und Verschlüsselung

### Was verschlüsselt ist

Alles, was ein Benutzer an Nutzdaten hinterlässt: Stunden, WBS-Elemente,
Personalnummer, Gewichtungen, das Auswertungsergebnis der PDFs sowie die abgelegten
PDF- und XLSX-Dateien. Verfahren ist AES-256-GCM mit einem eigenen Schlüssel je
Benutzer, der selbst nur eingewickelt in der Datenbank liegt.

Im Klartext bleiben Datumsangaben, Zeitstempel, der Buchungsstatus und die
Konto-Stammdaten — sie werden zum Filtern und Anmelden gebraucht.

Zur Kontrolle am laufenden System:

```bash
docker compose exec -T postgres pg_dump -U fckcats fckcats | grep -c "DEO"
# 0 -- kein WBS-Element steht im Klartext in der Sicherung
```

### Was der Schutz leistet

Er greift gegen jeden, der an die **ruhenden** Daten kommt: kopierte Volumes,
Datenbank-Dumps, Backups, ausgebaute Platten.

Er greift **nicht** gegen Zugriff auf den laufenden Prozess. Während einer Sitzung
liegt der Schlüssel im Arbeitsspeicher, weil die PDF-Auswertung und die Verteilung auf
dem Server stattfinden. Wer `DATA_MASTER_KEY` besitzt — also der Betreiber — kann alle
Workspaces öffnen, sofern der Benutzer keine eigene Passphrase gesetzt hat.

### Eigene Passphrase

Unter **Datenschutz** kann jeder Benutzer eine Passphrase setzen. Der Datenschlüssel
wird dann nur noch mit ihr ausgewickelt; `DATA_MASTER_KEY` öffnet diesen Workspace
nicht mehr. Nach jeder Anmeldung wird sie einmal abgefragt und gilt für den
Browser-Tab.

**Es gibt keine Wiederherstellung.** Geht die Passphrase verloren, sind die Daten
dieses Benutzers verloren — genau das ist der Zweck. Als Administrator lässt sich
weder die Passphrase zurücksetzen noch der Workspace öffnen; nur das Konto und seine
Daten löschen.

### Speichermodus

Ebenfalls unter **Datenschutz** wählt jeder Benutzer, ob überhaupt gespeichert wird.
Bei *nichts speichern* arbeitet die Anwendung als reines Import/Export-Werkzeug: das
PDF wird ausgewertet, die XLSX ausgeliefert, danach bleibt nichts zurück. Beim
Umschalten wird der vorhandene Bestand gelöscht — die CATS-Config bleibt.

### Umgang mit Sicherungen

```bash
# Datenbank (enthaelt nur verschluesselte Nutzdaten)
docker compose exec -T postgres pg_dump -U fckcats fckcats | gzip > fckcats-$(date +%F).sql.gz

# Dateien (PDFs und XLSX, ebenfalls verschluesselt)
docker run --rm -v fckcats_data:/data -v "$PWD":/backup alpine \
    tar czf /backup/fckcats-data-$(date +%F).tar.gz -C /data .
```

Beides ist ohne `DATA_MASTER_KEY` wertlos — und mit ihm vollständig lesbar. Den
Schlüssel deshalb an einem anderen Ort aufbewahren als die Sicherungen.

### Bestehende Installation aktualisieren

Wer eine ältere Fassung mit unverschlüsselten Daten betreibt, braucht nichts weiter zu
tun: Beim Start überführt die Anwendung vorhandene Daten selbsttätig, verschlüsselt
bereits abgelegte Dateien nach und entfernt die Klartextspalten. Das Protokoll zeigt
es an:

```
fckcats.migrate: Klartextdaten gefunden in: cats_config, workday, … -- wird verschluesselt.
fckcats.migrate: 202 Datensaetze verschluesselt, Klartextspalten entfernt.
fckcats.migrate: 7 abgelegte Dateien nachtraeglich verschluesselt.
```

Vor dem Aktualisieren eine Sicherung anlegen und `DATA_MASTER_KEY` **vorher** setzen —
sonst wird mit dem `SECRET_KEY` verschlüsselt, und ein späterer Wechsel macht die Daten
unlesbar.

## 7. SAML einrichten

Unter **Einstellungen → SAML Single Sign-on**:

| Feld | Inhalt |
|---|---|
| IdP Entity ID | Entity-ID des Identity Providers |
| IdP SSO-URL | Redirect-Endpunkt des IdP |
| IdP-Zertifikat | X.509 des IdP, Base64 (mit oder ohne PEM-Header) |
| SP Entity ID | frei wählbar, z. B. `https://fckcats.example.org` |
| ACS-URL | `https://fckcats.example.org/api/auth/saml/acs` |

Das Attribut-Mapping ist einstellbar; Vorgabe ist `uid` für den Benutzernamen,
`email` und `displayName`. **Der Benutzername ist der Workspace-Schlüssel** — er muss
je Person stabil und eindeutig sein.

Die SP-Metadata für den IdP liegt unter:

```
https://fckcats.example.org/api/auth/saml/metadata
```

Erst nach vollständiger Konfiguration den Haken *SAML aktivieren* setzen; unvollständige
Angaben werden abgelehnt. Auf dem Login-Bildschirm erscheint dann eine
SSO-Schaltfläche, der lokale Zugang bleibt daneben bestehen.

Assertions müssen signiert sein. Wenn der IdP zusätzlich das Response-Envelope
signiert, lässt sich das über `want_messages_signed` erzwingen.

## 8. Betrieb

**Status und Logs**

```bash
docker compose ps
docker compose logs -f api
curl -s http://localhost:${HTTP_PORT:-80}/api/health
```

**Aktualisieren**

```bash
git pull
docker compose up -d --build
```

Schemaänderungen werden beim Start idempotent angewandt; ein manueller
Migrationsschritt entfällt.

**Sichern.** Zu sichern sind die Datenbank und das Volume `data`:

```bash
docker compose exec -T postgres pg_dump -U fckcats fckcats | gzip > fckcats-$(date +%F).sql.gz
docker run --rm -v fckcats_data:/data -v "$PWD":/backup alpine \
    tar czf /backup/fckcats-data-$(date +%F).tar.gz -C /data .
```

Die Sicherungen enthalten personenbezogene Daten und gehören verschlüsselt abgelegt.

**Wiederherstellen**

```bash
gunzip -c fckcats-2026-08-18.sql.gz | docker compose exec -T postgres psql -U fckcats fckcats
```

**Zurücksetzen** (löscht alle Daten unwiderruflich):

```bash
docker compose down -v
```

## 9. Betrieb hinter einem vorgelagerten Proxy

Läuft bereits ein Reverse Proxy auf dem Host, den Stack auf freie Ports legen und
TLS im vorgelagerten Proxy terminieren:

```bash
HTTP_PORT=8080
HTTPS_PORT=8443
```

Der Proxy muss `Host` und `X-Forwarded-For` weiterreichen. Wichtig für SAML: die
**ACS-URL in der SAML-Konfiguration muss die von außen sichtbare URL sein**, nicht die
interne. Die Anwendung leitet Schema und Host für die Assertion-Prüfung aus diesem
Feld ab, damit Port-Mappings die Signaturprüfung nicht zerlegen.

## 10. Fehlersuche

**`SECRET_KEY fehlt`** beim Start — `.env` fehlt oder die Variable ist nicht gesetzt.
Compose liest `.env` aus dem Verzeichnis, in dem der Befehl läuft.

**Port ist belegt** — `Bind for 0.0.0.0:80 failed: port is already allocated`.
Belegte Ports zeigt `ss -ltn`; alternative Ports über `.env` setzen.

**`api` bleibt unhealthy** — `docker compose logs api`. Meist ist die Datenbank noch
nicht bereit; der Healthcheck von `postgres` hält `api` zurück, bis sie antwortet.

**TLS greift nicht** — nginx liest das Zertifikat ausschließlich beim Start.
`docker compose restart nginx`, dann `docker compose logs nginx | head -3`.

**PDF wird nicht erkannt** — die Auswertung braucht die Tabellenüberschrift mit
`von`, `bis`, `Std.` und `Sollz`. Zur Kontrolle:

```bash
docker compose exec api pdftotext -layout /data/<user-id>/uploads/<datei>.pdf -
```

Fehlt die Überschrift, ordnet die Anwendung die Werte der Reihe nach zu und meldet
das als Warnung in der Vorschau.

**`Die Daten konnten mit diesem Schlüssel nicht entschlüsselt werden`** — der
`DATA_MASTER_KEY` weicht von dem ab, mit dem verschlüsselt wurde. Den ursprünglichen
Schlüssel wiederherstellen; ohne ihn sind die Daten nicht zu retten.

**`Der Workspace ist mit einer Passphrase gesperrt`** — der Benutzer hat eine eigene
Passphrase gesetzt und muss sie nach der Anmeldung eingeben. Als Administrator lässt
sich das weder umgehen noch zurücksetzen.

**Alle Tage landen im Klärfall** — der Zeitnachweis verwendet andere Grundtexte als
die eingebaute Ausschlussliste. Die Entscheidung einmal treffen und *künftig
automatisch* ankreuzen; sie steht danach in der CATS-Config und lässt sich dort
ändern.
