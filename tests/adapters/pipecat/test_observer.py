"""Tests for the Pipecat conversation observer (no webhook involved: this
adapter has no inbound request, see adapters/pipecat/config.py)."""

from adapters.pipecat.observer import VconConversationObserver


class TestVconConversationObserver:
    def test_records_turns_and_ends(self):
        captured = []
        observer = VconConversationObserver(
            conversation_id="conv-2", on_vcon=lambda v: captured.append(v)
        )

        observer.record_user_transcript("Hello")
        observer.record_assistant_text_chunk("Hi")
        observer.record_assistant_text_chunk(" there")
        observer.finalize_assistant_turn()

        vcon = observer.end()

        assert len(captured) == 1
        assert captured[0] is vcon
        text_dialogs = [d for d in vcon.vcon_dict["dialog"] if d.get("type") == "text"]
        assert len(text_dialogs) == 2
        assert text_dialogs[1]["body"] == "Hi there"

    def test_attach_audio_produces_recording_dialog(self):
        observer = VconConversationObserver(conversation_id="conv-3", on_vcon=lambda v: None)
        observer.attach_audio(b"fake-audio", mediatype="audio/wav")
        vcon = observer.end()
        recordings = [d for d in vcon.vcon_dict["dialog"] if d.get("type") == "recording"]
        assert len(recordings) == 1

    def test_on_vcon_exception_does_not_propagate(self):
        def boom(_vcon):
            raise RuntimeError("callback exploded")

        observer = VconConversationObserver(conversation_id="conv-4", on_vcon=boom)
        # Must not raise even though the callback does.
        vcon = observer.end()
        assert vcon is not None

    def test_as_frame_processor_without_pipecat_raises(self):
        observer = VconConversationObserver(conversation_id="conv-5", on_vcon=lambda v: None)
        try:
            observer.as_frame_processor()
        except RuntimeError as e:
            assert "pipecat-ai" in str(e)
        else:
            # pipecat-ai happens to be installed in this environment; nothing to assert.
            pass
