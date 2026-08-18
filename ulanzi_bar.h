#pragma once
// ---------------------------------------------------------------------------
// Ulanzi (AWTRIX) full-screen knob bar, generated on the ESP32 at call time.
//
// The Magic Dial publishes the knob value straight to `ulanzi_aa68/custom/test`
// on the shared EMQX broker, so there is no Home Assistant round trip and no
// per-frame `shell_command` (~140 ms) in the path - the bar tracks the knob.
//
// The frame is a 52x16 discrete column bar: the first `round(pct/100*52)`
// columns are fully lit in the accent color, the rest sit at #0D1F27.
//
// FLASH BUDGET - this is the whole point of the design. Nothing is precomputed:
//   * no per-level / per-color base64 blobs (53 levels x N colors would have been
//     tens of KB of rodata)
//   * no 256-entry CRC-32 lookup table; the CRC is done bitwise, which costs
//     ~7.5 k shift/xor pairs per frame (tens of microseconds at 240 MHz) and
//     zero bytes of flash or .bss
//   * base64 comes from mbedtls, which ESP-IDF already links for TLS, so it is
//     free here
// Everything else is a stack/heap temporary that lives only for the call.
//
// PNG strategy - the device has no zlib and no image library, so the container is
// written out by hand:
//   * color type 3 (indexed) with a 2-entry PLTE  -> 1 byte per pixel
//   * a single "stored" (uncompressed) deflate block, which is legal zlib and
//     needs nothing but a length header and an Adler-32 checksum
// Result: ~930 B of PNG / ~1.3 kB of base64 payload. A truecolor frame would be
// 2.6 kB / 3.5 kB - identical flash cost, but 3.5 kB per frame at 5 fps risks
// overrunning the Ulanzi's MQTT receive buffer, so indexed is the default.
// ---------------------------------------------------------------------------

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <string>

#if defined(__has_include)
#if __has_include(<mbedtls/base64.h>)
#define ULANZI_BAR_HAS_MBEDTLS 1
#include <mbedtls/base64.h>
#endif
#endif

namespace ulanzi_bar {

constexpr int WIDTH = 52;
constexpr int HEIGHT = 16;

// Unlit part of the bar. Matches the preview that was signed off on the device.
constexpr uint8_t BG_R = 0x0D;
constexpr uint8_t BG_G = 0x1F;
constexpr uint8_t BG_B = 0x27;

// Table-free CRC-32 (reflected polynomial 0xEDB88320), the variant PNG uses.
// Deliberately not table-driven: a 256-entry table would be 1 kB of .bss plus the
// init code, and the whole frame is under 1 kB so the bitwise loop is cheaper
// overall than owning the table.
inline uint32_t crc32_update(uint32_t crc, const uint8_t *data, size_t len) {
  for (size_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (int k = 0; k < 8; k++)
      crc = (crc & 1u) ? (0xEDB88320u ^ (crc >> 1)) : (crc >> 1);
  }
  return crc;
}

inline uint32_t adler32(const uint8_t *data, size_t len) {
  uint32_t a = 1, b = 0;
  for (size_t i = 0; i < len; i++) {
    a = (a + data[i]) % 65521u;
    b = (b + a) % 65521u;
  }
  return (b << 16) | a;
}

inline void push_be32(std::string &out, uint32_t v) {
  out.push_back((char) ((v >> 24) & 0xFF));
  out.push_back((char) ((v >> 16) & 0xFF));
  out.push_back((char) ((v >> 8) & 0xFF));
  out.push_back((char) (v & 0xFF));
}

// tag + data, prefixed with the length and followed by the CRC-32 of tag+data.
inline void push_chunk(std::string &out, const char *tag, const std::string &data) {
  push_be32(out, (uint32_t) data.size());
  const size_t start = out.size();
  out.append(tag, 4);
  out.append(data);
  const uint32_t crc =
      crc32_update(0xFFFFFFFFu, (const uint8_t *) out.data() + start, out.size() - start) ^ 0xFFFFFFFFu;
  push_be32(out, crc);
}

inline std::string base64(const uint8_t *data, size_t len) {
  const size_t cap = 4 * ((len + 2) / 3) + 1;  // + NUL that mbedtls always writes
  std::string out;
  out.resize(cap);
#ifdef ULANZI_BAR_HAS_MBEDTLS
  size_t olen = 0;
  if (mbedtls_base64_encode((unsigned char *) &out[0], cap, &olen, data, len) != 0)
    return std::string();
  out.resize(olen);
#else
  // Host-test fallback only; the device always takes the mbedtls path above.
  static const char *ALPHABET =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  size_t o = 0, i = 0;
  for (; i + 2 < len; i += 3) {
    const uint32_t v = ((uint32_t) data[i] << 16) | ((uint32_t) data[i + 1] << 8) | data[i + 2];
    out[o++] = ALPHABET[(v >> 18) & 0x3F];
    out[o++] = ALPHABET[(v >> 12) & 0x3F];
    out[o++] = ALPHABET[(v >> 6) & 0x3F];
    out[o++] = ALPHABET[v & 0x3F];
  }
  if (i < len) {
    const uint32_t v = ((uint32_t) data[i] << 16) | ((i + 1 < len) ? ((uint32_t) data[i + 1] << 8) : 0);
    out[o++] = ALPHABET[(v >> 18) & 0x3F];
    out[o++] = ALPHABET[(v >> 12) & 0x3F];
    out[o++] = (i + 1 < len) ? ALPHABET[(v >> 6) & 0x3F] : '=';
    out[o++] = '=';
  }
  out.resize(o);
#endif
  return out;
}

// S = V = 1 HSV -> RGB, used for the light-strip hue mode so the bar shows the
// hue that is actually being dialed in. Computed, not looked up.
inline void hsv_to_rgb(float hue, uint8_t &r, uint8_t &g, uint8_t &b) {
  hue = fmodf(fmodf(hue, 360.0f) + 360.0f, 360.0f);
  const float h = hue / 60.0f;
  const int seg = (int) h;
  const float f = h - (float) seg;
  const uint8_t hi = 255;
  const uint8_t up = (uint8_t) lroundf(f * 255.0f);
  const uint8_t down = (uint8_t) lroundf((1.0f - f) * 255.0f);
  switch (seg % 6) {
    case 0: r = hi;   g = up;   b = 0;    break;
    case 1: r = down; g = hi;   b = 0;    break;
    case 2: r = 0;    g = hi;   b = up;   break;
    case 3: r = 0;    g = down; b = hi;   break;
    case 4: r = up;   g = 0;    b = hi;   break;
    default: r = hi;  g = 0;    b = down; break;
  }
}

// Wraps raw scanlines in a zlib stream made of one stored (uncompressed) deflate
// block. No compressor, no window, no tables.
inline std::string deflate_stored(const std::string &raw) {
  std::string z;
  z.reserve(raw.size() + 11);
  z.push_back((char) 0x78);  // CMF: deflate, 32 kB window
  z.push_back((char) 0x01);  // FLG: no dict, fastest -> (0x7801 % 31) == 0
  z.push_back((char) 0x01);  // BFINAL = 1, BTYPE = 00 (stored)
  const size_t n = raw.size();
  z.push_back((char) (n & 0xFF));
  z.push_back((char) ((n >> 8) & 0xFF));
  z.push_back((char) (~n & 0xFF));
  z.push_back((char) ((~n >> 8) & 0xFF));
  z.append(raw);
  push_be32(z, adler32((const uint8_t *) raw.data(), raw.size()));
  return z;
}

// 52x16 indexed PNG. Palette index 0 = accent color, 1 = background.
// The 848-byte scanline buffer is filled in a loop here and freed on return.
inline std::string png(int cols_on, uint8_t r, uint8_t g, uint8_t b) {
  if (cols_on < 0) cols_on = 0;
  if (cols_on > WIDTH) cols_on = WIDTH;

  std::string raw;  // HEIGHT * (1 filter byte + WIDTH indices) = 848 B
  raw.reserve((size_t) HEIGHT * (WIDTH + 1));
  for (int y = 0; y < HEIGHT; y++) {
    raw.push_back(0);  // filter type 0 = None
    for (int x = 0; x < WIDTH; x++) raw.push_back((char) (x < cols_on ? 0 : 1));
  }

  std::string ihdr;
  push_be32(ihdr, (uint32_t) WIDTH);
  push_be32(ihdr, (uint32_t) HEIGHT);
  ihdr.push_back((char) 8);  // bit depth
  ihdr.push_back((char) 3);  // color type 3 = indexed
  ihdr.push_back((char) 0);  // deflate
  ihdr.push_back((char) 0);  // adaptive filtering
  ihdr.push_back((char) 0);  // no interlace

  std::string plte;
  plte.push_back((char) r);
  plte.push_back((char) g);
  plte.push_back((char) b);
  plte.push_back((char) BG_R);
  plte.push_back((char) BG_G);
  plte.push_back((char) BG_B);

  std::string out;
  out.reserve(raw.size() + 128);
  out.append("\x89PNG\r\n\x1a\n", 8);
  push_chunk(out, "IHDR", ihdr);
  push_chunk(out, "PLTE", plte);
  push_chunk(out, "IDAT", deflate_stored(raw));
  push_chunk(out, "IEND", std::string());
  return out;
}

// 52x16 truecolor PNG. Fallback path with the exact same flash cost - the 2496
// bytes of RGB are built in the same kind of runtime loop. Only the wire size
// differs (2.6 kB vs 934 B), which is why indexed is preferred.
inline std::string png_truecolor(int cols_on, uint8_t r, uint8_t g, uint8_t b) {
  if (cols_on < 0) cols_on = 0;
  if (cols_on > WIDTH) cols_on = WIDTH;

  std::string raw;  // HEIGHT * (1 + WIDTH * 3) = 2512 B
  raw.reserve((size_t) HEIGHT * (WIDTH * 3 + 1));
  for (int y = 0; y < HEIGHT; y++) {
    raw.push_back(0);
    for (int x = 0; x < WIDTH; x++) {
      const bool on = x < cols_on;
      raw.push_back((char) (on ? r : BG_R));
      raw.push_back((char) (on ? g : BG_G));
      raw.push_back((char) (on ? b : BG_B));
    }
  }

  std::string ihdr;
  push_be32(ihdr, (uint32_t) WIDTH);
  push_be32(ihdr, (uint32_t) HEIGHT);
  ihdr.push_back((char) 8);
  ihdr.push_back((char) 2);  // color type 2 = truecolor RGB
  ihdr.push_back((char) 0);
  ihdr.push_back((char) 0);
  ihdr.push_back((char) 0);

  std::string out;
  out.reserve(raw.size() + 128);
  out.append("\x89PNG\r\n\x1a\n", 8);
  push_chunk(out, "IHDR", ihdr);
  push_chunk(out, "IDAT", deflate_stored(raw));
  push_chunk(out, "IEND", std::string());
  return out;
}

// Flip to `false` if the Ulanzi ever refuses the indexed PNG. Flash cost is the
// same either way; this only trades MQTT payload size for decoder conservatism.
constexpr bool USE_PALETTE_PNG = true;

// Complete AWTRIX custom-app payload for `ulanzi_aa68/custom/test`.
inline std::string payload(float pct, uint8_t r, uint8_t g, uint8_t b, int duration) {
  if (!(pct >= 0.0f)) pct = 0.0f;  // also catches NaN
  if (pct > 100.0f) pct = 100.0f;
  const int cols_on = (int) lroundf((float) WIDTH * pct / 100.0f);
  const std::string raw = USE_PALETTE_PNG ? png(cols_on, r, g, b) : png_truecolor(cols_on, r, g, b);
  std::string out;
  out.reserve(raw.size() * 4 / 3 + 96);
  out.append("{\"image\":[{\"x\":0,\"y\":0,\"data\":\"data:image/png;base64,");
  out.append(base64((const uint8_t *) raw.data(), raw.size()));
  out.append("\"}],\"duration\":");
  out.append(std::to_string(duration));
  out.append("}");
  return out;
}

}  // namespace ulanzi_bar
