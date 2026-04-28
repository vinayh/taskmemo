import noble from "@stoprocent/noble";
import sharp from "sharp";
import type { Peripheral, Characteristic } from "@stoprocent/noble";

const BYTES_PER_LINE = 70;
const PRINT_WIDTH = BYTES_PER_LINE * 8;

// ESC/POS encoder for Phomemo M02 family.
// Adapted from https://github.com/davestewart/phomemo-cli/blob/main/src/services/print.js
// which credits https://github.com/vivier/phomemo-tools for the protocol.
async function encodePngToEscPos(pngBuffer: Buffer): Promise<Buffer> {
  const prepared = sharp(pngBuffer)
    .resize({ width: PRINT_WIDTH, fit: "contain", background: "white" })
    .flatten({ background: "white" })
    .greyscale()
    .threshold(160)
    .ensureAlpha();

  const meta = await prepared.metadata();
  const width = meta.width ?? PRINT_WIDTH;
  const height = meta.height ?? 0;
  if (width !== PRINT_WIDTH) {
    throw new Error(`Image width ${width} does not match printer width ${PRINT_WIDTH}`);
  }
  const raw = await prepared.raw().toBuffer();

  const out: number[] = [];
  out.push(27, 64); // ESC @ — initialize
  out.push(27, 97, 0); // ESC a 0 — left align
  out.push(31, 17, 2, 4); // proprietary header

  let line = 0;
  let remaining = height;
  while (remaining > 0) {
    const lines = Math.min(remaining, 256);

    // GS v 0 — raster bit image header
    out.push(29, 118, 48, 0);
    out.push(BYTES_PER_LINE, 0);
    out.push(lines - 1, 0);

    for (let l = 0; l < lines; l++) {
      for (let x = 0; x < BYTES_PER_LINE; x++) {
        let byte = 0;
        for (let bit = 0; bit < 8; bit++) {
          const px = x * 8 + bit;
          const i = (line * width + px) * 4;
          const r = raw[i];
          const a = raw[i + 3];
          if (r === 0 && a !== 0) byte |= 1 << (7 - bit);
        }
        if (byte === 0x0a) byte = 0x14; // avoid LF in stream
        out.push(byte);
      }
      line++;
    }
    remaining -= lines;
  }

  // Footer: feed lines and proprietary trailer
  out.push(27, 100, 2);
  out.push(27, 100, 2);
  out.push(31, 17, 8);
  out.push(31, 17, 14);
  out.push(31, 17, 7);
  out.push(31, 17, 9);

  return Buffer.from(out);
}

const SCAN_TIMEOUT_MS = 15000;

async function findPrinter(targetName: string): Promise<Peripheral> {
  await noble.waitForPoweredOnAsync(10000);

  await noble.startScanningAsync([], false);
  try {
    const deadline = Date.now() + SCAN_TIMEOUT_MS;
    for await (const peripheral of noble.discoverAsync()) {
      const name = peripheral.advertisement?.localName ?? "";
      if (name === targetName) return peripheral;
      if (Date.now() > deadline) break;
    }
  } finally {
    await noble.stopScanningAsync();
  }

  throw new Error(
    `Could not find BLE device "${targetName}" within ${SCAN_TIMEOUT_MS}ms.\n` +
      `- Is the printer powered on?\n` +
      `- Is it removed from System Settings → Bluetooth (must NOT be paired there)?\n` +
      `- Has the terminal/Bun been granted Bluetooth permission?`,
  );
}

async function getWritableCharacteristic(peripheral: Peripheral): Promise<Characteristic> {
  await peripheral.connectAsync();
  const { characteristics } = await peripheral.discoverAllServicesAndCharacteristicsAsync();
  const writable = characteristics.find((c) => c.properties.includes("write") || c.properties.includes("writeWithoutResponse"));
  if (!writable) throw new Error("Printer has no writable characteristic");
  return writable;
}

export interface PrintOptions {
  deviceName?: string;
}

export async function printPng(pngBuffer: Buffer, options: PrintOptions = {}): Promise<void> {
  const targetName = options.deviceName ?? process.env.PHOMEMO_DEVICE_NAME ?? "M02 Pro";
  const data = await encodePngToEscPos(pngBuffer);

  const peripheral = await findPrinter(targetName);
  let characteristic: Characteristic | null = null;
  try {
    characteristic = await getWritableCharacteristic(peripheral);
    const useWithoutResponse = characteristic.properties.includes("writeWithoutResponse");
    await characteristic.writeAsync(data, useWithoutResponse);
    // Phomemo printers need a brief moment after the buffer is sent before disconnect,
    // otherwise the tail of the print can be cut off.
    await Bun.sleep(500);
  } finally {
    try {
      await peripheral.disconnectAsync();
    } catch {
      // ignore disconnect failures — the print already happened
    }
  }
}
