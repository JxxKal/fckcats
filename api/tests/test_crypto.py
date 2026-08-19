"""Tests der Verschluesselungsschicht."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("DATA_MASTER_KEY", "b" * 64)

import crypto  # noqa: E402


def test_runde_reise():
    key = crypto.new_dek()
    blob = crypto.encrypt(key, b"7,83 Stunden")
    assert blob != b"7,83 Stunden"
    assert crypto.decrypt(key, blob) == b"7,83 Stunden"


def test_gleicher_klartext_ergibt_andere_blobs():
    # Zufaellige Nonce: sonst liesse sich aus gleichen Blobs auf gleiche Werte
    # schliessen, etwa auf identische Stundenzahlen.
    key = crypto.new_dek()
    assert crypto.encrypt(key, b"8.00") != crypto.encrypt(key, b"8.00")


def test_falscher_schluessel_wird_abgelehnt():
    blob = crypto.encrypt(crypto.new_dek(), b"geheim")
    try:
        crypto.decrypt(crypto.new_dek(), blob)
        assert False, "falscher Schluessel wurde akzeptiert"
    except crypto.CryptoError:
        pass


def test_manipulation_wird_erkannt():
    key = crypto.new_dek()
    blob = bytearray(crypto.encrypt(key, b"8.00"))
    blob[-1] ^= 0x01
    try:
        crypto.decrypt(key, bytes(blob))
        assert False, "Manipulation wurde nicht erkannt"
    except crypto.CryptoError:
        pass


def test_json_runde_reise():
    key = crypto.new_dek()
    value = {"wbs_element": "DEO1111-NP/PJ00-O51.0000", "hours": 4.0}
    assert crypto.decrypt_json(key, crypto.encrypt_json(key, value)) == value


def test_dek_wickeln_mit_master():
    dek = crypto.new_dek()
    salt = crypto.new_salt()
    wrap = crypto.derive_master_wrap_key(crypto.master_key_from_env(), salt)
    assert crypto.unwrap_dek(crypto.wrap_dek(dek, wrap), wrap) == dek


def test_dek_wickeln_mit_passphrase():
    dek = crypto.new_dek()
    salt = crypto.new_salt()
    wrap = crypto.derive_passphrase_wrap_key("meine-lange-passphrase", salt)
    assert crypto.unwrap_dek(crypto.wrap_dek(dek, wrap), wrap) == dek


def test_falsche_passphrase_oeffnet_nicht():
    dek = crypto.new_dek()
    salt = crypto.new_salt()
    wrapped = crypto.wrap_dek(dek, crypto.derive_passphrase_wrap_key("richtig-lang-genug", salt))
    try:
        crypto.unwrap_dek(wrapped, crypto.derive_passphrase_wrap_key("falsch-aber-lang", salt))
        assert False, "falsche Passphrase wurde akzeptiert"
    except crypto.CryptoError:
        pass


def test_unterschiedliches_salt_ergibt_anderen_schluessel():
    master = crypto.master_key_from_env()
    a = crypto.derive_master_wrap_key(master, crypto.new_salt())
    b = crypto.derive_master_wrap_key(master, crypto.new_salt())
    assert a != b


def test_blind_index_ist_stabil_und_schluesselgebunden():
    k1, k2 = crypto.new_dek(), crypto.new_dek()
    wbs = "DEO1111-NP/PJ00-O51.0000"
    assert crypto.blind_index(k1, wbs) == crypto.blind_index(k1, wbs)
    assert crypto.blind_index(k1, wbs) == crypto.blind_index(k1, f"  {wbs} ")
    assert crypto.blind_index(k1, wbs) != crypto.blind_index(k2, wbs)
    assert crypto.blind_index(k1, wbs) != crypto.blind_index(k1, "DEO2222-NP/PJ00-O51.0000")


def test_datei_liegt_verschluesselt_auf_der_platte(tmp_path=None):
    import tempfile
    key = crypto.new_dek()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "sheet.pdf")
        crypto.encrypt_file(key, b"%PDF-1.4 vertraulich", path)
        with open(path, "rb") as f:
            raw = f.read()
        assert not raw.startswith(b"%PDF")          # kein Klartext-Header
        assert b"vertraulich" not in raw
        assert crypto.decrypt_file(key, path) == b"%PDF-1.4 vertraulich"


def test_leerer_blob_wird_abgelehnt():
    try:
        crypto.decrypt(crypto.new_dek(), b"")
        assert False, "leerer Datenblock wurde akzeptiert"
    except crypto.CryptoError:
        pass
