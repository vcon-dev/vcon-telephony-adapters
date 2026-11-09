"""FilesystemPublisher signs media URLs only when given a key."""
import json
from pathlib import Path

from core.media_publisher import FilesystemPublisher, signature_for

VECTOR = json.loads((Path(__file__).parent / "fixtures" / "media-signature-vector.json").read_text())


def _audio(tmp_path):
    src = tmp_path / "in.mp3"
    src.write_bytes(b"audio")
    return src


def test_signature_matches_shared_vector():
    assert signature_for(VECTOR["file_name"], VECTOR["derived_hex"]) == VECTOR["signature"]


def test_signed_url_carries_sig(tmp_path):
    pub = FilesystemPublisher(tmp_path / "out", base_url="https://media.test/rec", signing_key=VECTOR["derived_hex"])
    published = pub.publish(_audio(tmp_path), VECTOR["file_name"])
    assert published.url == f"https://media.test/rec/{VECTOR['file_name']}?sig={VECTOR['signature']}"
    assert (tmp_path / "out" / VECTOR["file_name"]).read_bytes() == b"audio"


def test_unsigned_url_unchanged_without_key(tmp_path):
    pub = FilesystemPublisher(tmp_path / "out", base_url="https://media.test/rec")
    assert pub.publish(_audio(tmp_path), "a.mp3").url == "https://media.test/rec/a.mp3"


def test_empty_key_counts_as_unset(tmp_path):
    pub = FilesystemPublisher(tmp_path / "out", base_url="https://media.test/rec", signing_key="")
    assert "sig=" not in pub.publish(_audio(tmp_path), "a.mp3").url


def test_config_reads_signing_key(monkeypatch, tmp_path):
    from core.base_config import BaseConfig
    monkeypatch.setenv("MEDIA_BACKEND", "filesystem")
    monkeypatch.setenv("MEDIA_FILESYSTEM_PATH", str(tmp_path))
    monkeypatch.setenv("MEDIA_BASE_URL", "https://media.test/rec")
    monkeypatch.setenv("MEDIA_URL_SIGNING_KEY", "k")
    assert BaseConfig().build_publisher().signing_key == "k"
    monkeypatch.delenv("MEDIA_URL_SIGNING_KEY")
    assert BaseConfig().build_publisher().signing_key is None
