#!/usr/bin/env python3
import argparse
import json
import threading
import time
import zlib
from pathlib import Path

import paho.mqtt.client as mqtt
from PIL import Image


def rgb565le_to_png(raw: bytes, width: int, height: int, output: Path) -> None:
    pixels = bytearray(width * height * 3)
    for i in range(width * height):
        value = raw[i * 2] | (raw[i * 2 + 1] << 8)
        pixels[i * 3] = ((value >> 11) & 0x1F) * 255 // 31
        pixels[i * 3 + 1] = ((value >> 5) & 0x3F) * 255 // 63
        pixels[i * 3 + 2] = (value & 0x1F) * 255 // 31
    Image.frombytes("RGB", (width, height), bytes(pixels)).save(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--broker", default="192.168.31.111")
    parser.add_argument("--output", type=Path, default=Path("magic_dial_screenshot.png"))
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()

    complete = threading.Event()
    meta = {}
    chunks: dict[int, bytes] = {}
    error: list[str] = []

    def on_connect(client, userdata, flags, reason_code, properties):
        client.subscribe("magic_dial/debug/screenshot/#")
        client.publish("magic_dial/debug/screenshot/request", "1")

    def on_message(client, userdata, message):
        nonlocal meta
        if message.topic.endswith("/meta"):
            meta = json.loads(message.payload)
            chunks.clear()
            return
        if "/chunk/" in message.topic:
            chunks[int(message.topic.rsplit("/", 1)[1])] = bytes(message.payload)
            return
        if message.topic.endswith("/done") and meta:
            raw = b"".join(chunks[i] for i in range(meta["chunks"]))
            crc = zlib.crc32(raw) & 0xFFFFFFFF
            if len(raw) != meta["bytes"] or f"{crc:08x}" != meta["crc32"]:
                error.append(f"invalid screenshot: bytes={len(raw)} crc32={crc:08x}")
            else:
                rgb565le_to_png(raw, meta["width"], meta["height"], args.output)
            complete.set()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.broker, 1883, 30)
    client.loop_start()
    try:
        if not complete.wait(args.timeout):
            raise TimeoutError("screenshot timed out")
        if error:
            raise RuntimeError(error[0])
        print(args.output.resolve())
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
