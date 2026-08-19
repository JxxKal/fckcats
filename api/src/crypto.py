"""Verschluesselung der Nutzdaten.

Schluesselhierarchie
--------------------
Jeder Benutzer hat einen eigenen Datenschluessel (DEK, 32 Byte, zufaellig).
Der DEK selbst liegt nur eingewickelt in der Datenbank:

  * Modus ``master``     -- eingewickelt mit einem Schluessel, der per HKDF aus
    ``DATA_MASTER_KEY`` (aus der .env) und einem benutzereigenen Salt abgeleitet
    wird. Die Anwendung kommt ohne Zutun des Benutzers an die Daten.
  * Modus ``passphrase`` -- eingewickelt mit einem Schluessel, der per scrypt aus
    einer Passphrase abgeleitet wird, die nur der Benutzer kennt. Ohne sie
    kommt niemand an die Daten, auch der Betreiber nicht.

Wogegen das schuetzt
--------------------
Gegen jeden, der an die ruhenden Daten kommt: kopierte Volumes, Datenbank-Dumps,
Backups, ausgebaute Platten. Im Modus ``master`` reicht dem Betreiber allerdings
die .env, um zu entschluesseln; erst eine Passphrase sperrt ihn aus.

Wogegen es nicht schuetzt
-------------------------
Gegen Zugriff auf den laufenden Prozess. Waehrend einer Sitzung liegt der DEK im
Arbeitsspeicher, weil PDF-Auswertung und Verteilung auf dem Server passieren.
Auch mit Passphrase ist das kein Ende-zu-Ende-Schutz -- dafuer muesste die
gesamte Verarbeitung im Browser laufen.

Was im Klartext bleibt
----------------------
Fremdschluessel, Datumsangaben, Zeitstempel und der Buchungsstatus. Sie werden
zum Filtern gebraucht. Ein Angreifer mit Datenbankzugriff sieht also, an welchen
Tagen jemand gearbeitet hat und wann exportiert wurde -- aber weder Stundenzahl
noch WBS-Element, Personalnummer oder den Inhalt der PDFs.
"""
from __future__ import annotations

import json
import os
import secrets
from typing import Any

from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

DEK_BYTES = 32
NONCE_BYTES = 12
SALT_BYTES = 16

# scrypt-Parameter fuer die Passphrase. n=2**15 braucht rund 32 MB und etwa
# 100 ms -- teuer genug gegen Durchprobieren, ertraeglich beim Anmelden.
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1

HKDF_INFO = b"fckcats-dek-wrap-v1"


class CryptoError(Exception):
    """Entschluesselung fehlgeschlagen -- falscher Schluessel oder manipulierte Daten."""


# ── Schluesselableitung ──────────────────────────────────────────────────────

def master_key_from_env() -> bytes:
    """Liest DATA_MASTER_KEY. Faellt auf SECRET_KEY zurueck, damit bestehende
    Installationen ohne neue Variable weiterlaufen."""
    raw = os.environ.get("DATA_MASTER_KEY") or os.environ.get("SECRET_KEY", "")
    if not raw:
        raise RuntimeError("Weder DATA_MASTER_KEY noch SECRET_KEY gesetzt.")
    # Beliebige Zeichenkette auf 32 Byte bringen; hex wird direkt genutzt.
    try:
        key = bytes.fromhex(raw)
        if len(key) >= DEK_BYTES:
            return key[:DEK_BYTES]
    except ValueError:
        pass
    digest = hashes.Hash(hashes.SHA256())
    digest.update(raw.encode())
    return digest.finalize()


def derive_master_wrap_key(master: bytes, salt: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=DEK_BYTES,
        salt=salt,
        info=HKDF_INFO,
    ).derive(master)


def derive_passphrase_wrap_key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(
        salt=salt, length=DEK_BYTES, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P
    ).derive(passphrase.encode())


def new_dek() -> bytes:
    return secrets.token_bytes(DEK_BYTES)


def new_salt() -> bytes:
    return secrets.token_bytes(SALT_BYTES)


# ── Symmetrische Verschluesselung ────────────────────────────────────────────

def encrypt(key: bytes, plaintext: bytes, aad: bytes | None = None) -> bytes:
    """AES-256-GCM. Ergebnis ist nonce || ciphertext-mit-tag."""
    nonce = secrets.token_bytes(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def decrypt(key: bytes, blob: bytes, aad: bytes | None = None) -> bytes:
    if not blob or len(blob) <= NONCE_BYTES:
        raise CryptoError("Datenblock ist zu kurz oder leer.")
    try:
        return AESGCM(key).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], aad)
    except Exception as e:
        raise CryptoError(f"Entschluesselung fehlgeschlagen: {type(e).__name__}") from e


def encrypt_json(key: bytes, value: Any) -> bytes:
    return encrypt(key, json.dumps(value, separators=(",", ":"), default=str).encode())


def decrypt_json(key: bytes, blob: bytes) -> Any:
    return json.loads(decrypt(key, blob))


# ── DEK ein- und auswickeln ──────────────────────────────────────────────────

def wrap_dek(dek: bytes, wrap_key: bytes) -> bytes:
    return encrypt(wrap_key, dek)


def unwrap_dek(wrapped: bytes, wrap_key: bytes) -> bytes:
    dek = decrypt(wrap_key, bytes(wrapped))
    if len(dek) != DEK_BYTES:
        raise CryptoError("Ausgewickelter Schluessel hat die falsche Laenge.")
    return dek


# ── Blind Index ──────────────────────────────────────────────────────────────

def blind_index(key: bytes, value: str) -> bytes:
    """Deterministischer Fingerabdruck fuer Eindeutigkeit und Gruppierung.

    Ein verschluesseltes Feld taugt nicht fuer UNIQUE oder GROUP BY, weil jede
    Verschluesselung anders aussieht. Der HMAC ist dagegen stabil und ohne den
    DEK nicht umkehrbar -- WBS-Elemente haetten fuer sich genommen zu wenig
    Entropie, um einem blossen Hash standzuhalten.
    """
    mac = hmac.HMAC(key, hashes.SHA256())
    mac.update(value.strip().encode())
    return mac.finalize()


# ── Dateien ──────────────────────────────────────────────────────────────────

def encrypt_file(key: bytes, data: bytes, path: str) -> None:
    """Schreibt die Datei verschluesselt. Nichts landet im Klartext auf der Platte."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(encrypt(key, data))
    os.replace(tmp, path)


def decrypt_file(key: bytes, path: str) -> bytes:
    with open(path, "rb") as f:
        return decrypt(key, f.read())
