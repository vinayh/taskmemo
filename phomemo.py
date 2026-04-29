"""Phomemo M02 Pro thermal-printer driver (BLE).

Encode a Pillow image to ESC/POS and print it over Bluetooth Low Energy.
Standalone: depends only on Pillow + Bleak. No knowledge of how the image
was generated.

    from PIL import Image
    import phomemo

    img = Image.open("foo.png")  # width must equal phomemo.PRINT_WIDTH
    phomemo.print_image(img)

The BLE transport has several non-obvious quirks specific to the M02 Pro
firmware; see CLAUDE.md in this repo for the long version.
"""

from __future__ import annotations

import asyncio
import sys
import time

from PIL import Image

# M02 Pro is 300 DPI on 53 mm paper -> 560 printable dots wide.
# Other Phomemo models in the M02 family use the same protocol with a
# different width: M02 / M02 Pro = 384 dots, M02S = 576, M03 = 832.
PRINT_WIDTH = 560
BYTES_PER_LINE = PRINT_WIDTH // 8

# Short UUID 0xff02 under service 0xff00 — the printer's data characteristic.
PHOMEMO_DATA_CHAR = "0000ff02-0000-1000-8000-00805f9b34fb"


def encode_escpos(image: Image.Image) -> bytes:
    """Encode a Pillow image to Phomemo ESC/POS bytes.

    Image is converted to 1-bit if it isn't already; black pixels print as
    ink. Width must equal PRINT_WIDTH.

    Matches https://github.com/vivier/phomemo-tools/blob/master/tools/phomemo-filter.py
    which is the upstream reverse-engineering reference for the M02 family.
    """
    if image.mode != "1":
        image = image.convert("1")
    width, height = image.size
    if width != PRINT_WIDTH:
        raise ValueError(f"image width {width} != {PRINT_WIDTH}")
    pixels = image.load()

    out = bytearray()
    # Header: ESC @, ESC a 0x01 (CENTER align — vivier uses center, not left),
    # then proprietary 1f 11 02 04.
    out += b"\x1b\x40\x1b\x61\x01\x1f\x11\x02\x04"

    line = 0
    remaining = height
    while remaining > 0:
        lines = min(remaining, 256)
        # GS v 0: 0x1d 0x76 0x30 m=0 xL xH yL yH
        out += b"\x1d\x76\x30\x00"
        out += BYTES_PER_LINE.to_bytes(2, "little")
        out += (lines - 1).to_bytes(2, "little")
        for _ in range(lines):
            for x in range(BYTES_PER_LINE):
                byte = 0
                for bit in range(8):
                    px_x = x * 8 + bit
                    if px_x < width and pixels[px_x, line] == 0:
                        byte |= 1 << (7 - bit)
                # 0x0a alone is interpreted as LF by the printer; 0x14 prints
                # the same bit pattern without that side-effect.
                if byte == 0x0a:
                    byte = 0x14
                out.append(byte)
            line += 1
        remaining -= lines

    # Footer: feed twice, then proprietary trigger sequence.
    out += b"\x1b\x64\x02\x1b\x64\x02"
    out += b"\x1f\x11\x08\x1f\x11\x0e\x1f\x11\x07\x1f\x11\x09"
    return bytes(out)


def print_image(
    image: Image.Image,
    device_name: str = "M02 Pro",
    *,
    chunk_size: int | None = None,
    chunk_delay_ms: float = 40.0,
    scan_timeout_s: float = 15.0,
    debug: bool = False,
) -> None:
    """Encode `image` and print it on the named Phomemo over BLE.

    chunk_size defaults to (negotiated MTU - 3 ATT header bytes).
    chunk_delay_ms paces the BLE writes; the M02 Pro's per-write buffer
    drops bytes if fed faster than ~10 KB/s for dense rasters, so the
    defaults intentionally cap at ~6 KB/s.
    """
    print_bytes(
        encode_escpos(image),
        device_name=device_name,
        chunk_size=chunk_size,
        chunk_delay_ms=chunk_delay_ms,
        scan_timeout_s=scan_timeout_s,
        debug=debug,
    )


def print_bytes(
    data: bytes,
    device_name: str = "M02 Pro",
    *,
    chunk_size: int | None = None,
    chunk_delay_ms: float = 40.0,
    scan_timeout_s: float = 15.0,
    debug: bool = False,
) -> None:
    """Send pre-encoded ESC/POS bytes to the printer. Most callers want print_image."""
    asyncio.run(
        _print_async(
            data,
            device_name=device_name,
            chunk_size=chunk_size,
            chunk_delay_ms=chunk_delay_ms,
            scan_timeout_s=scan_timeout_s,
            debug=debug,
        )
    )


async def _print_async(
    data: bytes,
    *,
    device_name: str,
    chunk_size: int | None,
    chunk_delay_ms: float,
    scan_timeout_s: float,
    debug: bool,
) -> None:
    # Lazy import: callers that only want encode_escpos shouldn't pull in
    # Bleak's pyobjc-* macOS dependency just by importing this module.
    from bleak import BleakClient, BleakScanner

    if debug:
        print(f"[phomemo] scanning for '{device_name}'…", file=sys.stderr)
    device = await BleakScanner.find_device_by_name(device_name, timeout=scan_timeout_s)
    if device is None:
        raise RuntimeError(
            f"BLE device '{device_name}' not found within {scan_timeout_s:.0f}s. "
            f"Is the printer powered on, and does the host have Bluetooth permission?"
        )

    if debug:
        print(f"[phomemo] connecting to {device.name} ({device.address})", file=sys.stderr)
        head = " ".join(f"{b:02x}" for b in data[:32])
        tail = " ".join(f"{b:02x}" for b in data[-32:])
        print(f"[phomemo] head: {head}", file=sys.stderr)
        print(f"[phomemo] tail: {tail}", file=sys.stderr)

    async with BleakClient(device) as client:
        if debug:
            print(f"[phomemo] connected (mtu={client.mtu_size})", file=sys.stderr)

        target_char = None
        for service in client.services:
            for char in service.characteristics:
                if char.uuid.lower() == PHOMEMO_DATA_CHAR:
                    target_char = char
                    break
            if target_char:
                break
        if target_char is None:
            raise RuntimeError("Phomemo write characteristic ff02 not found on this device")

        mtu = client.mtu_size or 23
        effective_chunk = chunk_size if chunk_size is not None else mtu - 3
        chunk_delay_s = chunk_delay_ms / 1000.0
        total_chunks = (len(data) + effective_chunk - 1) // effective_chunk

        if debug:
            print(
                f"[phomemo] writing {len(data)}B to {target_char.uuid} "
                f"(properties={target_char.properties}) — "
                f"{total_chunks} × {effective_chunk}B chunks, "
                f"{chunk_delay_ms:.0f}ms inter-chunk delay",
                file=sys.stderr,
            )

        # writeWithoutResponse only — the M02 Pro firmware ignores
        # write-with-response data even though the LL ACKs it.
        t0 = time.time()
        for i in range(0, len(data), effective_chunk):
            chunk = data[i : i + effective_chunk]
            await client.write_gatt_char(target_char, chunk, response=False)
            if chunk_delay_s > 0:
                await asyncio.sleep(chunk_delay_s)
        if debug:
            print(
                f"[phomemo] write_gatt_char stream finished in {time.time() - t0:.2f}s",
                file=sys.stderr,
            )

        # Wait for the printer to physically print before tearing down the
        # connection. M02 Pro at 300 DPI prints ~177 lines/sec; pad to 120.
        approx_lines = max(1, (len(data) - 30) // BYTES_PER_LINE)
        wait_s = max(2, approx_lines / 120 + 1)
        if debug:
            print(f"[phomemo] sleeping {wait_s:.1f}s for print to finish", file=sys.stderr)
        await asyncio.sleep(wait_s)
