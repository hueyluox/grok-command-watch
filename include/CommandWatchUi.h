// SPDX-License-Identifier: MIT
#pragma once

#include <M5GFX.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>

namespace watch_ui {

// 466 round AMOLED. Hierarchy: one number, then four marks, then a hairline ring.
constexpr int kWidth = 466;
constexpr int kHeight = 466;
constexpr int kCx = 233;
constexpr int kCy = 233;
constexpr int kOuterR = 228;
constexpr int kInnerR = 108;
constexpr int kMaxPads = 10;
constexpr float kPi = 3.14159265f;

enum class SlotState : uint8_t { Empty, Idle, Running, NeedsYou, Complete, Error };

struct State {
  std::array<SlotState, kMaxPads> slots{};
  char titles[kMaxPads][25]{};
  int8_t count = 0;
  int8_t selected = 0;
  int8_t focused = -1;
  uint8_t link = 0;
  int8_t battery = -1;
  bool charging = false;
  bool leftPressed = false;
  bool rightPressed = false;
  bool showAnswers = false;
  bool recording = false;
  int powerOverlay = 0;
  float powerHold = 0;
  uint32_t nowMs = 0;
  uint32_t selectAtMs = 0;
  uint32_t leftAtMs = 0;
  uint32_t rightAtMs = 0;
};

inline uint16_t rgb(uint8_t r, uint8_t g, uint8_t b) {
  return static_cast<uint16_t>(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3));
}

inline uint16_t mix(uint16_t a, uint16_t b, float t) {
  if (t <= 0) return a;
  if (t >= 1) return b;
  const int ar = (a >> 11) & 31, ag = (a >> 5) & 63, ab = a & 31;
  const int br = (b >> 11) & 31, bg = (b >> 5) & 63, bb = b & 31;
  const int r = ar + static_cast<int>((br - ar) * t);
  const int g = ag + static_cast<int>((bg - ag) * t);
  const int bl = ab + static_cast<int>((bb - ab) * t);
  return static_cast<uint16_t>((r << 11) | (g << 5) | bl);
}

inline float clampf(float v, float lo, float hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

inline float pulse01(uint32_t now, uint32_t periodMs) {
  const float t = (now % periodMs) / static_cast<float>(periodMs);
  return 0.5f - 0.5f * cosf(t * kPi * 2.0f);
}

inline float decay(uint32_t now, uint32_t start, uint32_t life) {
  if (start == 0 || now < start) return 0;
  const uint32_t dt = now - start;
  if (dt >= life) return 0;
  const float x = 1.0f - dt / static_cast<float>(life);
  return x * x;
}

inline uint16_t colorFor(SlotState s) {
  switch (s) {
    case SlotState::Idle:
      return rgb(142, 142, 147);
    case SlotState::Running:
      return rgb(70, 110, 230);
    case SlotState::NeedsYou:
      return rgb(255, 159, 10);
    case SlotState::Complete:
      return rgb(52, 220, 96);
    case SlotState::Error:
      return rgb(255, 69, 58);
    default:
      return rgb(44, 44, 46);
  }
}

// Instagram logo gradient: purple → magenta → pink → orange → gold.
inline uint16_t igColor(float t, float lift) {
  t = clampf(t, 0.0f, 1.0f);
  struct Stop {
    float p;
    uint8_t r, g, b;
  };
  // Quiet dusk loop: indigo → teal → ice → sand → indigo.
  static const Stop kStops[] = {
      {0.00f, 0x2A, 0x3A, 0x58}, {0.18f, 0x2F, 0x6B, 0x78}, {0.36f, 0x4A, 0x8F, 0x9A},
      {0.52f, 0x8F, 0xB8, 0xC0}, {0.68f, 0xD2, 0xC2, 0xA8}, {0.84f, 0x8A, 0x6E, 0x7A},
      {1.00f, 0x2A, 0x3A, 0x58},
  };
  const Stop* a = &kStops[0];
  const Stop* b = &kStops[1];
  for (int i = 0; i < 6; ++i) {
    if (t >= kStops[i].p && t <= kStops[i + 1].p) {
      a = &kStops[i];
      b = &kStops[i + 1];
      break;
    }
  }
  const float span = b->p - a->p;
  const float u = span > 0 ? (t - a->p) / span : 0;
  const float boost = clampf(0.78f + 0.22f * lift, 0.0f, 1.0f);
  const uint8_t cr = static_cast<uint8_t>(clampf((a->r + (b->r - a->r) * u) * boost, 0.0f, 255.0f));
  const uint8_t cg = static_cast<uint8_t>(clampf((a->g + (b->g - a->g) * u) * boost, 0.0f, 255.0f));
  const uint8_t cb = static_cast<uint8_t>(clampf((a->b + (b->b - a->b) * u) * boost, 0.0f, 255.0f));
  return rgb(cr, cg, cb);
}

inline const char* stateWord(SlotState s) {
  switch (s) {
    case SlotState::Running:
      return "RUN";
    case SlotState::NeedsYou:
      return "WAIT";
    case SlotState::Complete:
      return "DONE";
    case SlotState::Error:
      return "ERR";
    case SlotState::Idle:
      return "IDLE";
    default:
      return "";
  }
}

inline int liveCount(const State& ui) {
  if (ui.count > 0) {
    return ui.count > kMaxPads ? kMaxPads : static_cast<int>(ui.count);
  }
  int n = 0;
  for (int i = 0; i < kMaxPads; ++i) {
    if (ui.slots[static_cast<size_t>(i)] != SlotState::Empty) n = i + 1;
  }
  return n;
}

inline SlotState watchCell(const State& ui, int cell) {
  const int n = liveCount(ui);
  if (cell < 0 || cell >= n) return SlotState::Empty;
  return ui.slots[static_cast<size_t>(cell)];
}

inline SlotState commandState(const State& ui, int cmd) {
  return watchCell(ui, cmd);
}

inline int slotAtPoint(const State& ui, int x, int y) {
  const int n = liveCount(ui);
  if (n <= 0) return -1;
  const int dx = x - kCx;
  const int dy = y - kCy;
  const int r2 = dx * dx + dy * dy;
  if (r2 < kInnerR * kInnerR) return -1;
  if (r2 > kOuterR * kOuterR) return -1;
  float deg = atan2f(static_cast<float>(dy), static_cast<float>(dx)) * 180.0f / kPi;
  float shifted = deg + 90.0f;
  while (shifted < 0) shifted += 360.0f;
  while (shifted >= 360.0f) shifted -= 360.0f;
  const float step = 360.0f / static_cast<float>(n);
  int idx = static_cast<int>(floorf((shifted + step * 0.5f) / step));
  if (idx >= n) idx = 0;
  if (idx < 0) idx = n - 1;
  return idx;
}

inline int answerAtPoint(int x, int y) {
  const int dx = x - kCx;
  const int dy = y - kCy;
  if (dx * dx + dy * dy > kInnerR * kInnerR) return 0;
  if (dy < 0 && abs(dy) >= abs(dx)) return 1;
  if (dx >= 0 && abs(dx) >= abs(dy)) return 2;
  if (dy >= 0 && abs(dy) >= abs(dx)) return 3;
  return 4;
}

inline bool centerAtPoint(int x, int y) {
  const int dx = x - kCx;
  const int dy = y - kCy;
  return dx * dx + dy * dy <= kInnerR * kInnerR;
}

inline void polar(int r, float deg, int& x, int& y) {
  const float a = deg * kPi / 180.0f;
  x = kCx + static_cast<int>(r * cosf(a) + 0.5f);
  y = kCy + static_cast<int>(r * sinf(a) + 0.5f);
}

inline void ringSeg(M5Canvas& c, int r0, int r1, float d0, float d1, uint16_t color) {
  int x0, y0, x1, y1, x2, y2, x3, y3;
  polar(r0, d0, x0, y0);
  polar(r0, d1, x1, y1);
  polar(r1, d1, x2, y2);
  polar(r1, d0, x3, y3);
  c.fillTriangle(x0, y0, x1, y1, x2, y2, color);
  c.fillTriangle(x0, y0, x2, y2, x3, y3, color);
}

inline void drawBatteryRing(M5Canvas& c, const State& ui) {
  constexpr int kSeg = 180;
  constexpr float kStep = 360.0f / kSeg;
  constexpr int kR0 = 224;
  constexpr int kR1 = 216;
  const int midR = (kR0 + kR1) / 2;
  const int capR = (kR0 - kR1 + 1) / 2;
  const int pct = ui.battery < 0 ? 0 : ui.battery;
  const float fill = clampf(pct / 100.0f, 0.0f, 1.0f);
  const int lit = std::max(0, static_cast<int>(kSeg * fill + 0.5f));

  for (int i = 0; i < kSeg; ++i) {
    const float d0 = -90.0f + i * kStep;
    const float d1 = d0 + kStep + 0.45f;
    if (i < lit) {
      ringSeg(c, kR0, kR1, d0, d1, igColor((i + 0.5f) / static_cast<float>(kSeg), 0.2f));
    } else {
      ringSeg(c, kR0, kR1, d0, d1, rgb(28, 28, 30));
    }
  }
  if (lit > 0) {
    int sx, sy, ex, ey;
    polar(midR, -90.0f, sx, sy);
    polar(midR, -90.0f + lit * kStep, ex, ey);
    c.fillCircle(sx, sy, capR, igColor(0.0f, 0.2f));
    c.fillCircle(ex, ey, capR, igColor((lit - 0.5f) / static_cast<float>(kSeg), 0.2f));
  }
}

inline float hardBlink(uint32_t now, uint32_t periodMs) {
  return (now % periodMs) < (periodMs / 2) ? 1.0f : 0.12f;
}

inline uint16_t padFill(SlotState st, float breath, float blink) {
  switch (st) {
    case SlotState::Running:
      return mix(rgb(28, 48, 120), rgb(80, 120, 240), 0.40f + 0.50f * breath);
    case SlotState::NeedsYou:
      return mix(rgb(72, 40, 0), rgb(255, 159, 10), 0.50f + 0.40f * breath);
    case SlotState::Error:
      return rgb(255, 69, 58);
    case SlotState::Complete:
      return rgb(36, 196, 88);
    case SlotState::Idle:
      return rgb(52, 52, 56);
    default:
      return rgb(22, 22, 24);
  }
}

inline void render(M5Canvas& c, const State& ui) {
  c.fillScreen(0x0000);
  const int n = liveCount(ui);
  int selectedCmd = ui.selected;
  if (selectedCmd < 0 || selectedCmd >= n || watchCell(ui, selectedCmd) == SlotState::Empty) {
    selectedCmd = 0;
    for (int i = 0; i < n; ++i) {
      if (watchCell(ui, i) != SlotState::Empty) {
        selectedCmd = i;
        break;
      }
    }
  }
  const uint32_t now = ui.nowMs;
  const float breath = pulse01(now, 2400);
  const float blink = hardBlink(now, 700);
  const float say = std::max(ui.leftPressed ? 1.0f : 0.0f, decay(now, ui.leftAtMs, 380));
  const float send = std::max(ui.rightPressed ? 1.0f : 0.0f, decay(now, ui.rightAtMs, 260));
  const float pop = decay(now, ui.selectAtMs, 320);
  const SlotState faceState = n > 0 ? watchCell(ui, selectedCmd) : SlotState::Empty;

  drawBatteryRing(c, ui);

  const int padR = n >= 9 ? 32 : 38;
  constexpr int kPadOrbit = 168;
  const float step = n > 0 ? 360.0f / static_cast<float>(n) : 90.0f;
  for (int cmd = 0; cmd < n; ++cmd) {
    const SlotState st = watchCell(ui, cmd);
    if (st == SlotState::Empty) continue;
    const bool on = cmd == selectedCmd;
    const float mid = static_cast<float>(cmd) * step - 90.0f;
    const float rad = mid * kPi / 180.0f;
    const int lx = kCx + static_cast<int>(kPadOrbit * cosf(rad));
    const int ly = kCy + static_cast<int>(kPadOrbit * sinf(rad));

    c.fillCircle(lx, ly, padR, padFill(st, breath, blink));
    if (on) {
      c.drawCircle(lx, ly, padR + 3, rgb(255, 255, 255));
      c.drawCircle(lx, ly, padR + 4, rgb(255, 255, 255));
      if (pop > 0) {
        c.drawCircle(lx, ly, padR + 6 + static_cast<int>(14 * (1.0f - pop)),
                     mix(0x0000, rgb(255, 255, 255), pop));
      }
    } else {
      c.drawCircle(lx, ly, padR, rgb(20, 20, 22));
    }

    c.setTextDatum(middle_center);
    c.setFont(n >= 9 ? &fonts::FreeSansBold18pt7b : &fonts::FreeSansBold24pt7b);
    uint16_t ink = rgb(210, 210, 214);
    if (st == SlotState::Running) ink = mix(rgb(180, 200, 255), rgb(255, 255, 255), 0.40f + 0.50f * breath);
    else if (st == SlotState::Complete) ink = rgb(220, 255, 230);
    else if (st == SlotState::NeedsYou || st == SlotState::Error) ink = rgb(255, 255, 255);
    else if (st == SlotState::Idle) ink = rgb(152, 152, 157);
    c.setTextColor(ink);
    char mark[4];
    snprintf(mark, sizeof(mark), "%d", cmd + 1);
    c.drawString(mark, lx, ly + 2);
  }

  if (say > 0) {
    c.drawCircle(kCx, kCy, 78, mix(0x0000, rgb(255, 159, 10), say * 0.75f));
  } else if (send > 0) {
    c.drawCircle(kCx, kCy, 78, mix(0x0000, rgb(242, 242, 247), send * 0.45f));
  }

  c.setTextDatum(middle_center);
  const char* word = stateWord(faceState);
  if (word[0]) {
    c.setFont(&fonts::FreeSansBold18pt7b);
    uint16_t wc = colorFor(faceState);
    if (faceState == SlotState::Running) wc = mix(rgb(90, 130, 230), rgb(170, 195, 255), 0.40f + 0.50f * breath);
    c.setTextColor(wc);
    c.drawString(word, kCx, kCy - 10);
  } else {
    c.setFont(&fonts::FreeSans9pt7b);
    c.setTextColor(rgb(72, 72, 76));
    c.drawString("·", kCx, kCy - 10);
  }

  const char* title = ui.titles[selectedCmd];
  if (title && title[0] && faceState != SlotState::Empty) {
    char line[16];
    strncpy(line, title, 15);
    line[15] = 0;
    c.setFont(&fonts::Font0);
    c.setTextColor(rgb(152, 152, 157));
    c.drawString(line, kCx, kCy + 18);
  }

  if (ui.battery >= 0) {
    c.setFont(&fonts::Font0);
    c.setTextColor(ui.charging ? rgb(48, 209, 88) : rgb(99, 99, 102));
    char bat[8];
    snprintf(bat, sizeof(bat), "%d%%", ui.battery);
    c.drawString(bat, kCx, kCy + 38);
  }

  if (ui.powerOverlay) {
    c.fillCircle(kCx, kCy, 92, 0x0000);
    c.drawCircle(kCx, kCy, 92, rgb(255, 159, 10));
    c.setTextColor(rgb(255, 159, 10));
    c.setFont(&fonts::FreeSansBold18pt7b);
    c.drawString(ui.powerOverlay == 2 ? "OFF" : "HOLD", kCx, kCy);
  }
}

}  // namespace watch_ui
