#!/usr/bin/env python3
"""WebSocket client that sends mock PCM audio to vcon-realtime-server.

Tests the real-time transcription pipeline by connecting to the
WebSocket server and streaming synthetic audio data.
"""

import argparse
import asyncio
import json
import struct
import sys
import uuid

try:
    import websockets
except ImportError:
    print("Install websockets: pip install websockets")
    sys.exit(1)


def generate_silence(sample_rate: int, duration_ms: int) -> bytes:
    """Generate silence (zero-valued PCM samples)."""
    num_samples = int(sample_rate * duration_ms / 1000)
    return struct.pack(f"<{num_samples}h", *([0] * num_samples))


def generate_tone(sample_rate: int, duration_ms: int, frequency: float = 440.0) -> bytes:
    """Generate a sine wave tone as PCM16 samples."""
    import math

    num_samples = int(sample_rate * duration_ms / 1000)
    samples = []
    for i in range(num_samples):
        t = i / sample_rate
        value = int(16000 * math.sin(2 * math.pi * frequency * t))
        samples.append(max(-32768, min(32767, value)))
    return struct.pack(f"<{num_samples}h", *samples)


async def run_client(
    server_url: str,
    call_id: str,
    agent_id: str,
    sample_rate: int = 16000,
    duration_s: float = 5.0,
    chunk_ms: int = 20,
):
    """Connect to WebSocket server and stream audio."""
    print(f"Connecting to {server_url}")
    print(f"  Call ID: {call_id}")
    print(f"  Agent ID: {agent_id}")
    print(f"  Sample rate: {sample_rate} Hz")
    print(f"  Duration: {duration_s}s")
    print()

    async with websockets.connect(server_url) as ws:
        # Send start message
        start_msg = json.dumps({
            "type": "start",
            "call_id": call_id,
            "agent_id": agent_id,
            "sample_rate": sample_rate,
            "encoding": "pcm16",
            "channels": 1,
            "caller_number": "+15551234567",
            "callee_number": "+15559876543",
        })
        await ws.send(start_msg)
        print(f"Sent start message")

        # Wait for acknowledgment
        try:
            ack = await asyncio.wait_for(ws.recv(), timeout=5.0)
            print(f"Server response: {ack}")
        except asyncio.TimeoutError:
            print("No acknowledgment received (continuing anyway)")

        # Stream audio chunks
        total_chunks = int(duration_s * 1000 / chunk_ms)
        chunk_interval = chunk_ms / 1000.0

        print(f"Streaming {total_chunks} chunks ({chunk_ms}ms each)...")

        for i in range(total_chunks):
            # Alternate between tone and silence to simulate speech
            if (i // 25) % 2 == 0:  # 500ms speech, 500ms silence
                chunk = generate_tone(sample_rate, chunk_ms, frequency=440.0)
            else:
                chunk = generate_silence(sample_rate, chunk_ms)

            await ws.send(chunk)

            # Listen for any transcription results (non-blocking)
            try:
                result = await asyncio.wait_for(ws.recv(), timeout=0.001)
                data = json.loads(result) if isinstance(result, str) else result
                if isinstance(data, dict) and data.get("type") == "transcription":
                    text = data.get("text", "")
                    is_final = data.get("is_final", False)
                    marker = "[FINAL]" if is_final else "[partial]"
                    print(f"  {marker} {text}")
            except (asyncio.TimeoutError, Exception):
                pass

            await asyncio.sleep(chunk_interval)

        # Send stop message
        stop_msg = json.dumps({"type": "stop", "call_id": call_id})
        await ws.send(stop_msg)
        print(f"\nSent stop message")

        # Wait for final results
        print("Waiting for final results...")
        try:
            while True:
                result = await asyncio.wait_for(ws.recv(), timeout=3.0)
                if isinstance(result, str):
                    data = json.loads(result)
                    print(f"  Server: {json.dumps(data, indent=2)}")
                    if data.get("type") in ("end", "session_ended", "error"):
                        break
        except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
            pass

    print("\nWebSocket test complete.")


def main():
    parser = argparse.ArgumentParser(description="Test WebSocket client for vcon-realtime-server")
    parser.add_argument("--url", default="ws://localhost:9090/ws", help="WebSocket server URL")
    parser.add_argument("--call-id", default=None, help="Call ID (auto-generated if not set)")
    parser.add_argument("--agent-id", default="test-agent-001", help="Agent ID")
    parser.add_argument("--sample-rate", type=int, default=16000, help="Audio sample rate")
    parser.add_argument("--duration", type=float, default=5.0, help="Stream duration in seconds")
    parser.add_argument("--chunk-ms", type=int, default=20, help="Chunk size in milliseconds")
    args = parser.parse_args()

    call_id = args.call_id or str(uuid.uuid4())

    asyncio.run(run_client(
        server_url=args.url,
        call_id=call_id,
        agent_id=args.agent_id,
        sample_rate=args.sample_rate,
        duration_s=args.duration,
        chunk_ms=args.chunk_ms,
    ))


if __name__ == "__main__":
    main()
