#!/usr/bin/env python3
"""Durchlauf aller Endpunkte gegen eine laufende Instanz.

Die Unit-Tests pruefen Parser, Verteilung und XLSX-Erzeugung, fassen aber die
Datenbank nicht an. Dieser Test tut genau das -- und zwar moeglichst gegen
eine *frisch angelegte* Datenbank. Genau dort faellt auf, wenn Schema und Code
auseinanderlaufen: `CREATE TABLE IF NOT EXISTS` laesst eine gewachsene
Datenbank unveraendert, deshalb bleibt eine im Schema geloeschte Spalte auf dem
Entwicklungsrechner unbemerkt und schlaegt erst bei der Neuinstallation zu.

    python3 api/tests/smoke_test.py http://localhost:8080 admin StartPasswort

Das Konto muss Administratorrechte haben. Der Test legt einen eigenen Benutzer
an, arbeitet in dessen Workspace und laesst die vorhandenen Daten in Ruhe.
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

PDF = Path(__file__).parent / "fixtures" / "zeitnachweis_beispiel.pdf"

failures: list[tuple[str, int, object]] = []
base = ""


def call(method, path, body=None, token=None, raw=False):
    req = urllib.request.Request(base + path, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(body).encode() if body is not None else None
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=60) as r:
            payload = r.read()
            return r.status, (payload if raw else json.loads(payload or b"null"))
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload)
        except Exception:
            return e.code, payload.decode(errors="replace")[:400]


def upload(token):
    boundary = uuid.uuid4().hex
    buf = io.BytesIO()
    buf.write(f"--{boundary}\r\n".encode())
    buf.write(b'Content-Disposition: form-data; name="file"; filename="zeitnachweis.pdf"\r\n')
    buf.write(b"Content-Type: application/pdf\r\n\r\n")
    buf.write(PDF.read_bytes())
    buf.write(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(base + "/api/imports", method="POST", data=buf.getvalue())
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload)
        except Exception:
            return e.code, payload.decode(errors="replace")[:400]


def check(name, status, expected, response=None):
    allowed = expected if isinstance(expected, (list, tuple)) else [expected]
    ok = status in allowed
    print(f"  {'OK  ' if ok else 'FEHL'} {name:44} HTTP {status}")
    if not ok:
        failures.append((name, status, response))
        detail = response if isinstance(response, dict) else {}
        if detail.get("error_type"):
            print(f"       -> {detail['error_type']}: {detail.get('error_message')}")
        elif detail.get("detail"):
            print(f"       -> {str(detail['detail'])[:160]}")
    return ok


def main(argv: list[str]) -> int:
    global base
    if len(argv) < 4:
        print(__doc__)
        return 2
    base, admin_user, admin_pass = argv[1].rstrip("/"), argv[2], argv[3]
    suffix = uuid.uuid4().hex[:6]
    user = f"smoke{suffix}"

    print("=== Anmeldung ===")
    status, data = call("POST", "/api/auth/login",
                        {"username": admin_user, "password": admin_pass})
    if not check("Anmeldung als Administrator", status, 200, data):
        return 1
    adm = data["token"]
    if data.get("must_change_password"):
        print("       Hinweis: Passwortwechsel steht noch aus, Test bricht ab.")
        return 1

    print("\n=== Administration ===")
    status, data = call("GET", "/api/admin/users", token=adm)
    check("Benutzer auflisten", status, 200, data)
    status, data = call("POST", "/api/admin/users",
                        {"username": user, "password": "SmokeTestPasswort123"}, token=adm)
    check("Benutzer anlegen", status, 200, data)
    user_id = data.get("id") if isinstance(data, dict) else None
    if user_id:
        status, data = call("POST", f"/api/admin/users/{user_id}/password",
                            {"new_password": "SmokeZweitesPasswort1"}, token=adm)
        check("Passwort setzen", status, 200, data)
    check("SAML-Einstellungen lesen", *call("GET", "/api/admin/saml", token=adm)[:1], 200)
    status, data = call("GET", "/api/ssl/status", token=adm)
    check("TLS-Status", status, 200, data)

    print("\n=== Arbeiten im Workspace ===")
    status, data = call("POST", "/api/auth/login",
                        {"username": user, "password": "SmokeZweitesPasswort1"})
    check("Anmeldung des Testbenutzers", status, 200, data)
    status, data = call("POST", "/api/auth/password",
                        {"current_password": "SmokeZweitesPasswort1",
                         "new_password": "SmokeEigenesPasswort1"}, token=data["token"])
    check("Passwortwechsel", status, 200, data)
    tok = data["token"]

    check("Config lesen", *call("GET", "/api/cats-config", token=tok)[:1], 200)
    status, data = call("PUT", "/api/cats-config", {
        "personnel_number": "00123456",
        "wbs_elements": [{"wbs": "DEO1111-NP/PJ00-O51.0000", "weight": 60},
                         {"wbs": "DEO2222-NP/PJ00-O51.0000", "weight": 40}],
        "projects": [{"wbs": "PRJ-1/PJ00-O51.0000", "max_hours_per_week": 8}],
        "priority": "projects"}, token=tok)
    check("Config speichern", status, 200, data)

    print("\n=== Import ===")
    status, up = upload(tok)
    check("PDF hochladen", status, 200, up)
    check("Uploads auflisten", *call("GET", "/api/imports", token=tok)[:1], 200)
    if status == 200:
        status, data = call("POST", f"/api/imports/{up['upload_id']}/commit", {"clarifications": [
            {"work_date": "2026-03-05", "action": "book", "hours": None},
            {"work_date": "2026-03-06", "action": "book", "hours": 7.5}]}, token=tok)
        check("Import uebernehmen", status, 200, data)

    print("\n=== Zieltabelle und Export ===")
    check("Zieltabelle", *call("GET", "/api/entries", token=tok)[:1], 200)
    check("Arbeitstage", *call("GET", "/api/entries/workdays", token=tok)[:1], 200)
    check("Neu verteilen", *call("POST", "/api/entries/recalculate", token=tok)[:1], 200)
    status, data = call("POST", "/api/exports?date_from=2026-03-01&date_to=2026-03-31", token=tok)
    check("Export erzeugen", status, 200, data)
    export_id = data.get("export_id") if isinstance(data, dict) else None
    check("Export-Historie", *call("GET", "/api/exports", token=tok)[:1], 200)
    if export_id:
        check("XLSX herunterladen",
              *call("GET", f"/api/exports/{export_id}/download", token=tok, raw=True)[:1], 200)
        check("Export zuruecknehmen",
              *call("POST", f"/api/exports/{export_id}/revoke", token=tok)[:1], 200)
        check("Export loeschen",
              *call("DELETE", f"/api/exports/{export_id}", token=tok)[:1], 200)

    print("\n=== Historie, Speichermodus, Passphrase ===")
    check("Aenderungsprotokoll", *call("GET", "/api/entries/history", token=tok)[:1], 200)
    check("Protokoll loeschen",
          *call("DELETE", "/api/entries/history?confirm=true", token=tok)[:1], 200)
    check("Auf ephemeral schalten",
          *call("PUT", "/api/privacy/storage-mode",
                {"mode": "ephemeral", "confirm_purge": True}, token=tok)[:1], 200)
    status, up2 = upload(tok)
    check("Upload ohne Speicherung", status, 200, up2)
    if status == 200:
        days = [{"work_date": d["work_date"], "hours": d["hours_net"]}
                for d in up2["days"] if d["bookable"]]
        check("Direktexport",
              *call("POST", "/api/exports/direct", {"days": days}, token=tok, raw=True)[:1], 200)
    check("Zurueck auf persistent",
          *call("PUT", "/api/privacy/storage-mode", {"mode": "persistent"}, token=tok)[:1], 200)

    status, data = call("POST", "/api/privacy/passphrase",
                        {"passphrase": "smoke-test-passphrase"}, token=tok)
    check("Passphrase setzen", status, 200, data)
    check("ohne Schluessel gesperrt", *call("GET", "/api/cats-config", token=tok)[:1], 412)
    check("Entsperren", *call("POST", "/api/privacy/unlock",
                              {"passphrase": "smoke-test-passphrase"}, token=tok)[:1], 200)
    check("Passphrase entfernen",
          *call("DELETE", "/api/privacy/passphrase",
                {"passphrase": "smoke-test-passphrase"}, token=tok)[:1], 200)

    if user_id:
        call("PATCH", f"/api/admin/users/{user_id}", {"active": False}, token=adm)
        print(f"\nTestbenutzer '{user}' deaktiviert. Loeschen von Hand, falls gewuenscht.")

    print("\n" + "=" * 62)
    if failures:
        print(f"{len(failures)} Schritt(e) fehlgeschlagen:")
        for name, status, response in failures:
            print(f"  {name}: HTTP {status}")
            if isinstance(response, dict) and response.get("error_message"):
                print(f"    {response.get('error_type')}: {response['error_message']}")
        return 1
    print("Alle Schritte erfolgreich.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
