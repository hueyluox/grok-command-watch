// SPDX-License-Identifier: MIT
// Power / AMOLED rail mapping adapted from codex-micro-stopwatch (MIT).

#include <Arduino.h>
#include <M5PM1.h>
#include <M5Unified.h>

#include <cmath>

#include "CommandWatchBle.h"
#include "CommandWatchUi.h"
#include "PowerButtonGesture.h"
#include "TouchGesture.h"

namespace {

constexpr int kSwipeThresholdPx = 52;
constexpr uint8_t kButtonHapticIntensity = 176;
constexpr uint8_t kTouchHapticIntensity = 176;
constexpr uint32_t kWakeHapticDurationMs = 20;
constexpr uint32_t kButtonHapticDurationMs = 42;
constexpr uint32_t kTouchHapticDurationMs = 32;
constexpr uint32_t kNeedHapticDurationMs = 90;
constexpr uint32_t kDoneHapticDurationMs = 42;
constexpr uint32_t kErrorHapticDurationMs = 180;
constexpr bool kAlwaysOnDisplay = true;
constexpr uint32_t kBatteryDimAfterMs = 120000;
constexpr uint32_t kBatteryDeskSleepAfterMs = 300000;
constexpr uint32_t kDockDimAfterMs = 600000;
constexpr uint32_t kDockDeskSleepAfterMs = 1800000;
constexpr uint32_t kPowerTelemetryIntervalMs = 2000;
constexpr uint32_t kBatteryTelemetryIntervalMs = 30000;
constexpr uint32_t kPowerHoldPromptMs = 2000;
constexpr uint32_t kTravelPowerOffMs = 6000;
constexpr uint32_t kPowerButtonDoubleClickMs = 500;
constexpr uint32_t kCompleteHoldMs = 3000;
constexpr uint8_t kActiveBrightness = 120;
constexpr uint8_t kDimBrightness = 24;
constexpr uint8_t kIoeSharedL3bEnable = 7;
constexpr uint8_t kIoePanelReset = 4;
constexpr bool kCutSharedRailInDeskSleep = true;
constexpr uint32_t kCompletionChimeSampleRate = 12000;
constexpr uint32_t kCompletionChimeDurationMs = 270;
constexpr size_t kCompletionChimeSamples =
    kCompletionChimeSampleRate * kCompletionChimeDurationMs / 1000;
constexpr float kPi = 3.14159265358979323846f;
constexpr uint8_t kCompletionChimeVolume = 160;

watch_ble::Server ble;
watch_ble::Snapshot host;
M5Canvas canvas(&M5.Display);
M5PM1 powerManager;

bool leftPressed = false;
bool rightPressed = false;
bool touchTracking = false;
bool hapticActive = false;
bool deskSleeping = false;
bool displayRailOff = false;
bool speakerSuspended = false;
bool powerManagerReady = false;
bool powerButtonPollingReady = false;
bool powerButtonPressed = false;
bool powerButtonWokeDisplay = false;
bool pendingPowerClickWokeDisplay = false;
bool touchPowerHoldConsumed = false;
bool docked = false;
bool charging = false;
bool dockStateInitialized = false;
bool pendingDockState = false;
uint8_t pendingDockSamples = 0;
int8_t batteryPercent = -1;
bool watchRecording = false;
int16_t micFrame[2][320] = {};
int micWrite = 0;
bool micPrimed = false;
uint32_t recordStartedMs = 0;
constexpr uint32_t kMaxRecordMs = 25000;
int8_t selectedSlot = 0;
int powerOverlay = 0;
uint32_t selectAtMs = 0;
uint32_t leftAtMs = 0;
uint32_t rightAtMs = 0;
uint32_t lastFrameMs = 0;
uint8_t appliedBrightness = kActiveBrightness;
int16_t touchStartX = 0;
int16_t touchStartY = 0;
touch_gesture::Direction activeSwipe = touch_gesture::Direction::None;
uint32_t hapticUntilMs = 0;
uint32_t lastActivityMs = 0;
uint32_t lastBatteryMs = 0;
uint32_t lastPowerTelemetryMs = 0;
uint32_t touchStartedAtMs = 0;
uint32_t lastPowerOverlayDrawMs = 0;
std::array<int16_t, kCompletionChimeSamples> completionChimePcm = {};
std::array<watch_ui::SlotState, watch_ui::kMaxPads> lastStates{};
std::array<bool, watch_ui::kMaxPads> sawState{};
power_button_gesture::Detector powerButtonGesture(kPowerButtonDoubleClickMs);

void drawScreen();
void stopHaptic();
void emit(const char* op, int8_t slot = -1, int8_t n = 0, bool pttOn = false);
[[noreturn]] void enterTravelPowerOff();

void setPanelRail(bool enabled) {
  auto& ioe1 = M5.getIOExpander(0);
  ioe1.setHighImpedance(kIoeSharedL3bEnable, false);
  ioe1.setDirection(kIoeSharedL3bEnable, true);
  ioe1.digitalWrite(kIoeSharedL3bEnable, enabled);
}

void setPanelReset(bool high) {
  auto& ioe1 = M5.getIOExpander(0);
  ioe1.setHighImpedance(kIoePanelReset, false);
  ioe1.setDirection(kIoePanelReset, true);
  ioe1.digitalWrite(kIoePanelReset, high);
}

void startHaptic(uint8_t intensity, uint32_t durationMs) {
  M5.Power.setVibration(intensity);
  hapticActive = true;
  hapticUntilMs = millis() + durationMs;
}

void stopHaptic() {
  M5.Power.setVibration(0);
  hapticActive = false;
}

void updateHaptic() {
  if (!hapticActive || static_cast<int32_t>(millis() - hapticUntilMs) < 0) return;
  stopHaptic();
}

float softNoteEnvelope(float noteTime, float noteDuration) {
  constexpr float kAttackSeconds = 0.038f;
  constexpr float kReleaseSeconds = 0.100f;
  if (noteTime < 0.0f || noteTime >= noteDuration) return 0.0f;
  float envelope = 1.0f;
  if (noteTime < kAttackSeconds) {
    const float phase = noteTime / kAttackSeconds;
    const float fade = sinf(phase * kPi * 0.5f);
    envelope *= fade * fade;
  }
  const float releaseStart = noteDuration - kReleaseSeconds;
  if (noteTime > releaseStart) {
    const float phase = (noteTime - releaseStart) / kReleaseSeconds;
    const float fade = cosf(phase * kPi * 0.5f);
    envelope *= fade * fade;
  }
  return envelope;
}

void buildCompletionChime() {
  constexpr float kFirstFrequency = 440.00f;
  constexpr float kSecondFrequency = 554.37f;
  constexpr float kPeakAmplitude = 0.135f * 32767.0f;
  for (size_t sample = 0; sample < completionChimePcm.size(); ++sample) {
    const float time = static_cast<float>(sample) / kCompletionChimeSampleRate;
    const float first =
        sinf(2.0f * kPi * kFirstFrequency * time) * softNoteEnvelope(time, 0.145f);
    const float second = sinf(2.0f * kPi * kSecondFrequency * (time - 0.105f)) *
                         softNoteEnvelope(time - 0.105f, 0.165f);
    completionChimePcm[sample] =
        static_cast<int16_t>(kPeakAmplitude * (first + second));
  }
}

void playDoneChime() {
  M5.Speaker.playRaw(completionChimePcm.data(), completionChimePcm.size(),
                     kCompletionChimeSampleRate, false, 1, -1, true);
}

void enterDeskSleep() {
  if (deskSleeping || leftPressed || rightPressed || touchTracking) return;
  stopHaptic();
  speakerSuspended = M5.Speaker.isRunning();
  if (speakerSuspended) M5.Speaker.end();
  M5.Display.setBrightness(0);
  M5.Display.sleep();
  M5.Display.waitDisplay();
  if (kCutSharedRailInDeskSleep) {
    M5.Display.releaseBus();
    setPanelRail(false);
    displayRailOff = true;
  }
  appliedBrightness = 0;
  deskSleeping = true;
  Serial.printf("POWER desk_sleep dock=%d\n", docked ? 1 : 0);
}

void wakeDeskSleep() {
  if (!deskSleeping) return;
  if (displayRailOff) {
    setPanelRail(true);
    delay(10);
    setPanelReset(false);
    delay(8);
    setPanelReset(true);
    delay(2);
    if (!M5.Display.getPanel()->init(true)) {
      Serial.println("POWER panel reinit failed");
    }
    displayRailOff = false;
  } else {
    M5.Display.wakeup();
  }
  M5.Display.setRotation(2);
  M5.Display.setTextWrap(false);
  appliedBrightness = kActiveBrightness;
  M5.Display.setBrightness(appliedBrightness);
  if (speakerSuspended) {
    if (!M5.Speaker.begin()) Serial.println("POWER speaker resume failed");
    M5.Speaker.setVolume(kCompletionChimeVolume);
    speakerSuspended = false;
  }
  deskSleeping = false;
  drawScreen();
  Serial.println("POWER desk_wake");
}

bool noteActivity() {
  powerButtonGesture.cancel();
  pendingPowerClickWokeDisplay = false;
  lastActivityMs = millis();
  if (deskSleeping) {
    wakeDeskSleep();
    return false;
  }
  if (appliedBrightness == kActiveBrightness) return true;
  const bool wasOff = appliedBrightness == 0;
  appliedBrightness = kActiveBrightness;
  M5.Display.setBrightness(kActiveBrightness);
  if (wasOff) drawScreen();
  return !wasOff;
}

bool configureCodecSpeechGain() {
  constexpr uint8_t kAddr = 0x18;
  constexpr uint32_t kHz = 100000;
  m5gfx::i2c::i2c_temporary_switcher_t bus(1, 47, 48);
  const bool ok =
      M5.In_I2C.writeRegister8(kAddr, 0x17, 0xCB, kHz) &&
      M5.In_I2C.writeRegister8(kAddr, 0x16, 0x04, kHz) &&
      M5.In_I2C.writeRegister8(kAddr, 0x14, 0x18, kHz) &&
      M5.In_I2C.writeRegister8(kAddr, 0x1C, 0x6A, kHz);
  bus.restore();
  return ok;
}

void startWatchRecord() {
  if (watchRecording) return;
  if (M5.Speaker.isRunning()) {
    M5.Speaker.stop();
    M5.Speaker.end();
  }
  auto cfg = M5.Mic.config();
  cfg.sample_rate = 16000;
  cfg.input_channel = m5::input_only_right;
  cfg.over_sampling = 1;
  cfg.magnification = 2;
  M5.Mic.config(cfg);
  if (!M5.Mic.begin()) {
    Serial.println("MIC begin failed");
    emit("voice_err", selectedSlot);
    return;
  }
  static int16_t prime[320];
  M5.Mic.record(prime, 320, 16000);
  uint32_t until = millis() + 120;
  while (M5.Mic.isRecording() != 0 && static_cast<int32_t>(millis() - until) < 0) delay(1);
  M5.Mic.end();
  if (!M5.Mic.begin()) {
    emit("voice_err", selectedSlot);
    return;
  }
  configureCodecSpeechGain();
  watchRecording = true;
  micWrite = 0;
  micPrimed = false;
  recordStartedMs = millis();
  emit("voice_start", selectedSlot);
  Serial.println("MIC start");
}

void stopWatchRecord() {
  if (!watchRecording) return;
  watchRecording = false;
  uint32_t guard = millis() + 200;
  while (M5.Mic.isRecording() && static_cast<int32_t>(millis() - guard) < 0) delay(1);
  if (micPrimed) {
    ble.notifyAudio(reinterpret_cast<uint8_t*>(micFrame[micWrite ^ 1]), sizeof(micFrame[0]));
  }
  M5.Mic.end();
  M5.Speaker.begin();
  M5.Speaker.setVolume(kCompletionChimeVolume);
  emit("voice_stop", selectedSlot);
  Serial.println("MIC stop");
}

void pollWatchRecord() {
  if (!watchRecording) return;
  lastActivityMs = millis();
  if (millis() - recordStartedMs >= kMaxRecordMs) {
    stopWatchRecord();
    return;
  }
  if (M5.Mic.isRecording() >= 2) return;
  if (micPrimed) {
    ble.notifyAudio(reinterpret_cast<uint8_t*>(micFrame[micWrite ^ 1]), sizeof(micFrame[0]));
  }
  M5.Mic.record(micFrame[micWrite], 320, 16000);
  micWrite ^= 1;
  micPrimed = true;
}

void updateIdleDimming() {
  if (kAlwaysOnDisplay) {
    if (deskSleeping) wakeDeskSleep();
    if (appliedBrightness != kActiveBrightness) {
      appliedBrightness = kActiveBrightness;
      M5.Display.setBrightness(kActiveBrightness);
    }
    return;
  }
  if (watchRecording) lastActivityMs = millis();
  if (leftPressed || rightPressed || touchTracking) lastActivityMs = millis();
  const uint32_t idleMs = millis() - lastActivityMs;
  const uint32_t dimAfterMs = docked ? kDockDimAfterMs : kBatteryDimAfterMs;
  const uint32_t sleepAfterMs =
      docked ? kDockDeskSleepAfterMs : kBatteryDeskSleepAfterMs;
  if (idleMs >= sleepAfterMs) {
    enterDeskSleep();
    return;
  }
  if (deskSleeping) return;
  const uint8_t target = idleMs >= dimAfterMs ? kDimBrightness : kActiveBrightness;
  if (target != appliedBrightness) {
    appliedBrightness = target;
    M5.Display.setBrightness(target);
  }
}

void observeDockState(bool candidate) {
  if (dockStateInitialized && candidate == docked) {
    pendingDockSamples = 0;
    return;
  }
  if (pendingDockSamples == 0 || pendingDockState != candidate) {
    pendingDockState = candidate;
    pendingDockSamples = 1;
    return;
  }
  if (++pendingDockSamples < 2) return;
  const bool changed = !dockStateInitialized || docked != candidate;
  docked = candidate;
  dockStateInitialized = true;
  pendingDockSamples = 0;
  if (!changed) return;
  Serial.printf("POWER mode=%s\n", docked ? "dock" : "battery");
  if (docked) {
    lastActivityMs = millis();
    if (deskSleeping) wakeDeskSleep();
  }
}

void updatePowerTelemetry(bool force = false) {
  const uint32_t now = millis();
  if (force || lastBatteryMs == 0 || now - lastBatteryMs >= kBatteryTelemetryIntervalMs) {
    lastBatteryMs = now;
    int8_t next = -1;
    uint16_t vbat = 0;
    if (powerManagerReady && powerManager.readVbat(&vbat) == M5PM1_OK && vbat >= 2500 &&
        vbat <= 4500) {
      if (vbat <= 3300) next = 0;
      else if (vbat >= 4150) next = 100;
      else next = static_cast<int8_t>(((int)vbat - 3300) * 100 / 850);
    } else {
      const int level = M5.Power.getBatteryLevel();
      if (level >= 0) next = static_cast<int8_t>(constrain(level, 0, 100));
    }
    if (next != batteryPercent && next >= 0) ble.setBattery(static_cast<uint8_t>(next));
    batteryPercent = next;
    Serial.printf("BAT pct=%d vbat=%u charge=%d\n", (int)batteryPercent, (unsigned)vbat,
                  charging ? 1 : 0);
  }
  if (force || lastPowerTelemetryMs == 0 ||
      now - lastPowerTelemetryMs >= kPowerTelemetryIntervalMs) {
    lastPowerTelemetryMs = now;
    charging = M5.Power.isCharging() == m5::Power_Class::is_charging;
    const int vinMv = M5.Power.getVBUSVoltage();
    const bool candidateDock =
        dockStateInitialized && docked ? vinMv >= 3500 : vinMv >= 4000;
    observeDockState(candidateDock);
  }
}

watch_ui::State currentUi() {
  watch_ui::State ui = host.ui;
  ui.selected = selectedSlot;
  ui.battery = batteryPercent;
  ui.charging = charging;
  ui.leftPressed = leftPressed;
  ui.rightPressed = rightPressed;
  ui.link = host.available ? 2 : (ble.connected() ? 1 : 0);
  const watch_ui::SlotState st =
      selectedSlot >= 0 ? ui.slots[static_cast<size_t>(selectedSlot)]
                        : watch_ui::SlotState::Empty;
  ui.showAnswers = st == watch_ui::SlotState::NeedsYou;
  ui.recording = watchRecording;
  ui.powerOverlay = powerOverlay;
  ui.nowMs = millis();
  ui.selectAtMs = selectAtMs;
  ui.leftAtMs = leftAtMs;
  ui.rightAtMs = rightAtMs;
  return ui;
}

void drawScreen() {
  if (deskSleeping) return;
  watch_ui::render(canvas, currentUi());
  canvas.pushSprite(0, 0);
}

void emit(const char* op, int8_t slot, int8_t n, bool pttOn) {
  watch_ble::Event event;
  strncpy(event.op, op, sizeof(event.op) - 1);
  event.slot = slot;
  event.n = n;
  event.pttOn = pttOn;
  event.valid = true;
  ble.emit(event);
}

void setSlot(int8_t slot, bool focusMac) {
  if (slot < 0 || slot >= watch_ui::kMaxPads) return;
  selectedSlot = slot;
  selectAtMs = millis();
  startHaptic(kTouchHapticIntensity, kTouchHapticDurationMs);
  if (focusMac) emit("focus", selectedSlot);
  drawScreen();
}

void selectSlot(int8_t slot, bool focusMac) {
  if (slot < 0 || slot >= watch_ui::kMaxPads) return;
  setSlot(slot, focusMac);
}

void applyHostSnapshot() {
  const auto latest = ble.snapshot();
  if (!latest.available) return;
  for (int i = 0; i < watch_ui::kMaxPads; ++i) {
    const auto next = latest.ui.slots[static_cast<size_t>(i)];
    if (sawState[static_cast<size_t>(i)] && lastStates[static_cast<size_t>(i)] != next) {
      if (next == watch_ui::SlotState::NeedsYou) {
        noteActivity();
        startHaptic(kButtonHapticIntensity, kNeedHapticDurationMs);
      } else if (next == watch_ui::SlotState::Complete) {
        noteActivity();
        startHaptic(kButtonHapticIntensity, kDoneHapticDurationMs);
        playDoneChime();
      } else if (next == watch_ui::SlotState::Error) {
        noteActivity();
        startHaptic(kButtonHapticIntensity, kErrorHapticDurationMs);
      }
    }
    lastStates[static_cast<size_t>(i)] = next;
    sawState[static_cast<size_t>(i)] = true;
  }
  host = latest;
  if (host.ui.selected >= 0 && host.ui.selected < watch_ui::kMaxPads) {
    selectedSlot = host.ui.selected;
  }
  drawScreen();
}

void beginTouchGesture(int x, int y) {
  touchTracking = true;
  touchStartX = static_cast<int16_t>(x);
  touchStartY = static_cast<int16_t>(y);
  activeSwipe = touch_gesture::Direction::None;
  touchStartedAtMs = millis();
  touchPowerHoldConsumed = watch_ui::centerAtPoint(x, y);
}

void updateTouchGesture(int x, int y) {
  if (!touchTracking || activeSwipe != touch_gesture::Direction::None) return;
  const auto direction =
      touch_gesture::classifySwipe(x - touchStartX, y - touchStartY, kSwipeThresholdPx);
  if (direction == touch_gesture::Direction::None) return;
  activeSwipe = direction;
  if (direction == touch_gesture::Direction::Left) {
    emit("page", selectedSlot, -1);
  } else if (direction == touch_gesture::Direction::Right) {
    emit("page", selectedSlot, 1);
  }
}

void finishTouchGesture(int x, int y) {
  if (!touchTracking) return;
  touchTracking = false;
  if (touchPowerHoldConsumed && powerOverlay) {
    powerOverlay = 0;
    drawScreen();
    return;
  }
  if (activeSwipe != touch_gesture::Direction::None) {
    activeSwipe = touch_gesture::Direction::None;
    return;
  }
  const auto ui = currentUi();
  if (watch_ui::centerAtPoint(x, y)) {
    return;
  }
  const auto live = currentUi();
  const int slot = watch_ui::slotAtPoint(live, x, y);
  if (slot < 0) return;
  if (watch_ui::watchCell(live, slot) == watch_ui::SlotState::Empty) return;
  selectSlot(static_cast<int8_t>(slot), true);
}

void updateTouchPowerHold() {
  if (!touchTracking || !touchPowerHoldConsumed) return;
  if (activeSwipe != touch_gesture::Direction::None) return;
  const uint32_t heldMs = millis() - touchStartedAtMs;
  if (heldMs >= kPowerHoldPromptMs && powerOverlay == 0) {
    powerOverlay = 1;
    startHaptic(kButtonHapticIntensity, kButtonHapticDurationMs);
    drawScreen();
  }
  if (heldMs >= kTravelPowerOffMs) enterTravelPowerOff();
}

[[noreturn]] void enterTravelPowerOff() {
  powerOverlay = 2;
  drawScreen();
  startHaptic(kButtonHapticIntensity, 60);
  delay(70);
  stopHaptic();
  delay(480);
  M5.Speaker.stop();
  M5.Speaker.end();
  M5.Display.setBrightness(0);
  M5.Display.sleep();
  M5.Display.waitDisplay();
  Serial.println("POWER travel_off");
  Serial.flush();
  if (powerManagerReady) powerManager.shutdown();
  M5.Power.M5pm1.powerOff();
  delay(500);
  esp_restart();
  while (true) delay(1000);
}

void beginPowerButtonPress() {
  powerButtonPressed = true;
  powerButtonWokeDisplay = deskSleeping;
  lastActivityMs = millis();
  if (deskSleeping) {
    wakeDeskSleep();
    startHaptic(kButtonHapticIntensity, kWakeHapticDurationMs);
  }
}

void finishPowerButtonPress() {
  if (!powerButtonPressed) return;
  powerButtonPressed = false;
  const auto event = powerButtonGesture.release(millis());
  if (event == power_button_gesture::Event::DoubleClick) {
    pendingPowerClickWokeDisplay = false;
    enterTravelPowerOff();
  }
  pendingPowerClickWokeDisplay = powerButtonWokeDisplay;
  powerButtonWokeDisplay = false;
}

void updatePowerButton() {
  static uint32_t lastPollMs = 0;
  const uint32_t now = millis();
  if (powerButtonPollingReady) {
    const auto delayed = powerButtonGesture.poll(now);
    if (delayed == power_button_gesture::Event::SingleClick) {
      if (!kAlwaysOnDisplay && !pendingPowerClickWokeDisplay) enterDeskSleep();
      pendingPowerClickWokeDisplay = false;
    }
    if (lastPollMs != 0 && now - lastPollMs < 25) return;
    lastPollMs = now;
    bool pressed = false;
    if (powerManager.btnGetState(&pressed) != M5PM1_OK) return;
    if (pressed && !powerButtonPressed) beginPowerButtonPress();
    if (!pressed && powerButtonPressed) finishPowerButtonPress();
    return;
  }
  if (M5.BtnPWR.wasClicked()) {
    if (deskSleeping) {
      lastActivityMs = now;
      wakeDeskSleep();
    } else if (!kAlwaysOnDisplay) {
      enterDeskSleep();
    }
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(100);
  Serial.println("Grok Command Watch boot");

  auto config = M5.config();
  config.clear_display = true;
  config.internal_mic = true;
  M5.begin(config);
  M5.Display.setRotation(2);
  M5.Display.setBrightness(kActiveBrightness);
  M5.Display.setTextWrap(false);

  const m5pm1_err_t pm1Status = powerManager.begin(&M5.In_I2C);
  powerManagerReady = pm1Status == M5PM1_OK;
  if (powerManagerReady) {
    powerManager.timerClear();
    powerManager.setSingleResetDisable(true);
    const auto doubleOffStatus = powerManager.setDoubleOffDisable(true);
    bool doubleOffDisabled = false;
    const auto doubleOffRead = powerManager.getDoubleOffDisable(&doubleOffDisabled);
    powerButtonPollingReady = doubleOffStatus == M5PM1_OK &&
                              doubleOffRead == M5PM1_OK && doubleOffDisabled;
    if (!powerButtonPollingReady) powerManager.setDoubleOffDisable(false);
  }

  M5.Speaker.setVolume(kCompletionChimeVolume);
  buildCompletionChime();

  canvas.setColorDepth(16);
  canvas.setPsram(true);
  if (canvas.createSprite(M5.Display.width(), M5.Display.height()) == nullptr) {
    M5.Display.fillScreen(TFT_BLACK);
    M5.Display.setTextColor(TFT_RED);
    M5.Display.setTextDatum(middle_center);
    M5.Display.drawString("Canvas failed", M5.Display.width() / 2,
                          M5.Display.height() / 2);
    while (true) delay(1000);
  }
  canvas.setTextWrap(false);

  ble.begin();
  lastActivityMs = millis();
  updatePowerTelemetry(true);
  drawScreen();
  Serial.println("GROK_COMMAND_WATCH_READY");
}

void loop() {
  M5.update();
  updatePowerButton();
  updateHaptic();
  updatePowerTelemetry();
  updateIdleDimming();
  ble.pollHostSerial();
  if (ble.consumeDirty()) applyHostSnapshot();
  pollWatchRecord();

  const auto touch = M5.Touch.getDetail();
  if (touch.wasPressed()) {
    if (noteActivity()) beginTouchGesture(touch.x, touch.y);
    else startHaptic(kButtonHapticIntensity, kWakeHapticDurationMs);
  }
  if (touchTracking && touch.isPressed()) {
    updateTouchGesture(touch.x, touch.y);
    updateTouchPowerHold();
  }
  if (touch.wasReleased()) finishTouchGesture(touch.x, touch.y);

  if (M5.BtnA.wasPressed()) {
    noteActivity();
    leftPressed = true;
    leftAtMs = millis();
    startHaptic(kButtonHapticIntensity, kButtonHapticDurationMs);
    if (watchRecording) {
      if (millis() - recordStartedMs < 1800) Serial.println("MIC ignore short stop");
      else stopWatchRecord();
    } else {
      startWatchRecord();
    }
    drawScreen();
  }
  if (M5.BtnA.wasReleased()) {
    leftPressed = false;
    drawScreen();
  }
  if (M5.BtnB.wasPressed()) {
    noteActivity();
    rightPressed = true;
    rightAtMs = millis();
    startHaptic(kButtonHapticIntensity, kButtonHapticDurationMs);
    emit("send", selectedSlot);
    drawScreen();
  }
  if (M5.BtnB.wasReleased()) {
    rightPressed = false;
    drawScreen();
  }

  if (!deskSleeping) {
    const uint32_t now = millis();
    if (now - lastFrameMs >= 33) {
      lastFrameMs = now;
      drawScreen();
    }
  }
}
