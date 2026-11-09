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
    assert signature_for(VECTOR["file_name"], VECTOR["signing_key"]) == VECTOR["signature"]


def test_signed_url_carries_sig(tmp_path):
    pub = FilesystemPublisher(tmp_path / "out", base_url="https://media.test/rec", signing_key=VECTOR["signing_key"])
    published = pub.publish(_audio(tmp_path), VECTOR["file_name"])
    assert published.url == f"https://media.test/rec/{VECTOR['file_name']}?sig={VECTOR['signature']}"
    assert (tmp_path / "out" / VECTOR["file_name"]).read_bytes() == b"audio"


def test_unsigned_url_unchanged_without_key(tmp_path):
    pub = FilesystemPublisher(tmp_path / "out", base_url="https://media.test/rec")
    assert pub.publish(_audio(tmp_path), "a.mp3").url == "https://media.test/rec/a.mp3"


def test_empty_key_counts_as_unset(tmp_path):
    pub = FilesystemPublisher(tmp_path / "out", base_url="https://media.test/rec", signing_key="")
    assert "sig=" not in pub.publish(_audio(tmp_path), "a.mp3").url


def test_config_reads_signing_key(minimal_env, tmp_path):
    from core.base_config import BaseConfig
    monkeypatch = minimal_env   # CONSERVER_URL set here, not left over from another test
    monkeypatch.setenv("MEDIA_BACKEND", "filesystem")
    monkeypatch.setenv("MEDIA_FILESYSTEM_PATH", str(tmp_path))
    monkeypatch.setenv("MEDIA_BASE_URL", "https://media.test/rec")
    monkeypatch.setenv("MEDIA_URL_SIGNING_KEY", "k")
    assert BaseConfig().build_publisher().signing_key == "k"
    monkeypatch.delenv("MEDIA_URL_SIGNING_KEY")
    assert BaseConfig().build_publisher().signing_key is None


def test_publish_log_leaves_out_the_signature(tmp_path, monkeypatch, caplog):
    import logging

    from adapters.twilio.builder import TwilioRecordingData, TwilioVconBuilder

    monkeypatch.setattr(TwilioVconBuilder, "_download_recording", lambda self, rd: b"audio")
    pub = FilesystemPublisher(tmp_path / "out", base_url="https://media.test/rec", signing_key=VECTOR["signing_key"])
    builder = TwilioVconBuilder(recording_format="wav", publisher=pub)
    data = TwilioRecordingData({"RecordingSid": "RE1", "From": "+15551234567", "To": "+15559876543",
                                "Direction": "inbound", "RecordingUrl": "https://api.example.test/RE1"})
    with caplog.at_level(logging.INFO, logger="core.base_builder"):
        vcon = builder.build(data)
    url = vcon.to_dict()["dialog"][0]["url"]
    sig = url.split("?sig=", 1)[1]
    assert len(sig) == 64
    assert "Published RE1" in caplog.text and "https://media.test/rec/RE1.wav" in caplog.text
    assert sig not in caplog.text and "sig=" not in caplog.text
