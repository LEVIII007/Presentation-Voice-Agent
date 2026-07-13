"""Whether a TTS backend has native inline pause markup — the ONE capability
build_system_prompt needs from the TTS factory to decide whether to teach the
model a pause tag at all. See app/domain/ports.py: TTSFactory.pause_tag.
"""

from app.adapters.azure_tts import AzureTTSFactory
from app.adapters.pipecat_services import CartesiaTTSFactory


def test_cartesia_factory_returns_its_native_break_tag():
    factory = CartesiaTTSFactory(api_key="key", voice_id="voice")
    assert factory.pause_tag(0.6) == '<break time="0.6s" />'
    print("ok: Cartesia factory returns its native <break> tag syntax")


def test_azure_factory_has_no_native_pause_tag():
    # The V2 text-stream API this adapter uses has no SSML support, so this
    # backend must report no pause capability rather than emit markup Azure
    # would read aloud as literal words.
    factory = AzureTTSFactory(api_key="key", region="eastus", voice="voice")
    assert factory.pause_tag(0.6) is None
    print("ok: Azure factory reports no native pause tag")


if __name__ == "__main__":
    test_cartesia_factory_returns_its_native_break_tag()
    test_azure_factory_has_no_native_pause_tag()
    print("\nAll TTS pause-tag tests passed.")
