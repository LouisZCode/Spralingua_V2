"""
Here we load the Audio ins and outs with Voice Audio Detection (VAD). Right now we are using:

Silero inside and

You find here:
transport_vad              - Local mic/speaker (for local testing)
transport_websocket        - WebSocket server, single client (legacy)
transport_fastapi_ws       - FastAPI WebSocket, multi-client (current)
"""
from pipecat.transports.websocket.server import WebsocketServerTransport, WebsocketServerParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams


def transport_vad():
    # Local mic/speaker (pyaudio-backed, dev/local only). Imported lazily so the
    # server path — which never calls this — doesn't pull pyaudio into the image.
    from pipecat.transports.local.audio import (
        LocalAudioTransport,
        LocalAudioTransportParams,
    )

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


def transport_fastapi_ws(websocket):
    """Creates a transport for a single FastAPI WebSocket connection.
    Each client gets their own transport instance."""
    return FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            serializer=ProtobufFrameSerializer(),
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(stop_secs=1.5)
            ),
        ),
    )