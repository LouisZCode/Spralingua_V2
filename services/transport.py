"""
Here we load the Audio ins and autos with Voice Audio Detection (VAD). Right now we are using:

Silero inside and

You find here:
transport_vad        - Local mic/speaker (for local testing)
transport_websocket  - WebSocket server (for browser clients)
"""
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.transports.websocket.server import WebsocketServerTransport, WebsocketServerParams
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams


def transport_vad():
    return LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(stop_secs=1.5)  # Wait 1.5s of silence before ending utterance
            ),
        )
    )


def transport_websocket(host="0.0.0.0", port=8765):
    return WebsocketServerTransport(
        params=WebsocketServerParams(
            serializer=ProtobufFrameSerializer(),
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(stop_secs=1.5)
            ),
        ),
        host=host,
        port=port,
    )