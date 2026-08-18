// SPDX-License-Identifier: MIT
#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>
#include <BLE2902.h>
#include <BLEDevice.h>
#include <BLEHIDDevice.h>
#include <BLESecurity.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <HIDTypes.h>
#include <esp_mac.h>

#include <array>
#include <cstring>

#include "CommandWatchUi.h"

namespace watch_ble {

static constexpr char kDeviceName[] = "GrokWatch";
static constexpr char kServiceUuid[] = "a1c8e240-6f31-4b2a-9c11-0d8f1a7c0020";
static constexpr char kSnapshotUuid[] = "a1c8e240-6f31-4b2a-9c11-0d8f1a7c0021";
static constexpr char kEventUuid[] = "a1c8e240-6f31-4b2a-9c11-0d8f1a7c0022";
static constexpr char kAudioUuid[] = "a1c8e240-6f31-4b2a-9c11-0d8f1a7c0023";

static const uint8_t kHidReportMap[] = {
    USAGE_PAGE(1),      0x01,
    USAGE(1),           0x06,
    COLLECTION(1),      0x01,
    REPORT_ID(1),       0x01,
    USAGE_PAGE(1),      0x07,
    USAGE_MINIMUM(1),   0xE0,
    USAGE_MAXIMUM(1),   0xE7,
    LOGICAL_MINIMUM(1), 0x00,
    LOGICAL_MAXIMUM(1), 0x01,
    REPORT_SIZE(1),     0x01,
    REPORT_COUNT(1),    0x08,
    HIDINPUT(1),        0x02,
    REPORT_COUNT(1),    0x01,
    REPORT_SIZE(1),     0x08,
    HIDINPUT(1),        0x01,
    REPORT_COUNT(1),    0x06,
    REPORT_SIZE(1),     0x08,
    LOGICAL_MINIMUM(1), 0x00,
    LOGICAL_MAXIMUM(1), 0x65,
    USAGE_MINIMUM(1),   0x00,
    USAGE_MAXIMUM(1),   0x65,
    HIDINPUT(1),        0x00,
    END_COLLECTION(0),
};

struct Event {
  char op[16]{};
  int8_t slot = -1;
  int8_t n = 0;
  bool pttOn = false;
  bool valid = false;
};

struct Snapshot {
  watch_ui::State ui{};
  uint32_t receivedAtMs = 0;
  bool available = false;
};

class Server : public BLEServerCallbacks, public BLECharacteristicCallbacks {
 public:
  void begin() {
    uint8_t mac[6] = {0x28, 0x84, 0x85, 0x43, 0x9F, 0xE1};
    const esp_err_t macErr = esp_base_mac_addr_set(mac);
    BLEDevice::init(kDeviceName);
    BLEDevice::setPower(ESP_PWR_LVL_P9);
    server_ = BLEDevice::createServer();
    server_->setCallbacks(this);

    hid_ = new BLEHIDDevice(server_);
    hid_->manufacturer()->setValue("Grok Command Watch");
    hid_->pnp(0x02, 0x303A, 0x1002, 0x0100);
    hid_->hidInfo(0x00, 0x01);
    hid_->reportMap(const_cast<uint8_t*>(kHidReportMap), sizeof(kHidReportMap));
    hidInput_ = hid_->inputReport(1);
    hid_->startServices();

    BLEService* service = server_->createService(kServiceUuid);
    snapshot_ = service->createCharacteristic(
        kSnapshotUuid, BLECharacteristic::PROPERTY_WRITE |
                           BLECharacteristic::PROPERTY_WRITE_NR);
    snapshot_->setCallbacks(this);
    event_ = service->createCharacteristic(
        kEventUuid, BLECharacteristic::PROPERTY_NOTIFY |
                        BLECharacteristic::PROPERTY_READ);
    event_->addDescriptor(new BLE2902());
    audio_ = service->createCharacteristic(
        kAudioUuid, BLECharacteristic::PROPERTY_NOTIFY);
    audio_->addDescriptor(new BLE2902());
    service->start();
    BLEDevice::setMTU(517);

    BLESecurity* security = new BLESecurity();
    security->setAuthenticationMode(ESP_LE_AUTH_NO_BOND);
    security->setCapability(ESP_IO_CAP_NONE);

    BLEAdvertising* adv = BLEDevice::getAdvertising();
    BLEAdvertisementData advData;
    advData.setName(kDeviceName);
    advData.setAppearance(ESP_BLE_APPEARANCE_HID_KEYBOARD);
    adv->setAdvertisementData(advData);
    BLEAdvertisementData scanData;
    scanData.setName(kDeviceName);
    adv->setScanResponseData(scanData);
    adv->addServiceUUID(hid_->hidService()->getUUID());
    adv->addServiceUUID(kServiceUuid);
    adv->setScanResponse(true);
    adv->setMinPreferred(0x06);
    adv->setMaxPreferred(0x12);
    adv->start();
    hid_->setBatteryLevel(100);
    Serial.printf("BLE advertising as GrokWatch mac_err=%d\n", static_cast<int>(macErr));
  }

  void onConnect(BLEServer*) override {
    connected_ = true;
    Serial.println("BLE connect");
  }

  void onDisconnect(BLEServer*) override {
    connected_ = false;
    Serial.println("BLE disconnect");
    BLEDevice::startAdvertising();
    Serial.println("BLE advertising restart");
  }

  void onWrite(BLECharacteristic* characteristic) override {
    if (characteristic != snapshot_) return;
    applyHostJson(characteristic->getValue());
  }

  void applyHostJson(const std::string& value) {
    if (value.empty() || value.size() > 640) return;
    StaticJsonDocument<1536> doc;
    if (deserializeJson(doc, value.c_str(), value.size())) {
      Serial.println("snapshot parse fail");
      return;
    }
    JsonArray states = doc["s"].as<JsonArray>();
    const int incoming = doc["n"] | (states.isNull() ? 0 : static_cast<int>(states.size()));
    const bool clear = (doc["clr"] | 0) == 1;
    if (incoming <= 0 && !clear && snapshotState_.available && snapshotState_.ui.count > 0) {
      Serial.println("snapshot ignore empty");
      return;
    }
    Snapshot next;
    next.available = true;
    next.receivedAtMs = millis();
    next.ui.selected = doc["sel"] | -1;
    next.ui.focused = doc["fg"] | -1;
    next.ui.link = doc["link"] | 2;
    next.ui.pages = static_cast<int8_t>(doc["pages"] | 1);
    if (next.ui.pages < 1) next.ui.pages = 1;
    int parsed = 0;
    for (int i = 0; i < watch_ui::kMaxPads; ++i) {
      const int raw = states.isNull() ? 0 : states[i] | 0;
      next.ui.slots[static_cast<size_t>(i)] =
          static_cast<watch_ui::SlotState>(constrain(raw, 0, 6));
      if (!states.isNull() && i < static_cast<int>(states.size())) parsed = i + 1;
    }
    next.ui.count = static_cast<int8_t>(doc["n"] | parsed);
    if (next.ui.count < 0) next.ui.count = 0;
    if (next.ui.count > watch_ui::kMaxPads) next.ui.count = watch_ui::kMaxPads;
    JsonArray titles = doc["t"].as<JsonArray>();
    for (int i = 0; i < watch_ui::kMaxPads; ++i) {
      const char* text = titles.isNull() ? "" : titles[i] | "";
      strncpy(next.ui.titles[i], text, 24);
      next.ui.titles[i][24] = 0;
    }
    snapshotState_ = next;
    dirty_ = true;
  }

  void pollHostSerial() {
    while (Serial.available()) {
      const int raw = Serial.read();
      if (raw < 0) break;
      const char c = static_cast<char>(raw);
      if (c == '\n' || c == '\r') {
        if (hostLineLen_ > 0) {
          hostLine_[hostLineLen_] = 0;
          if (strncmp(hostLine_, "SNAP ", 5) == 0) applyHostJson(hostLine_ + 5);
          hostLineLen_ = 0;
        }
      } else if (hostLineLen_ + 1 < sizeof(hostLine_)) {
        hostLine_[hostLineLen_++] = c;
      } else {
        hostLineLen_ = 0;
      }
    }
  }

  bool connected() const { return connected_; }
  bool consumeDirty() {
    const bool d = dirty_;
    dirty_ = false;
    return d;
  }
  Snapshot snapshot() const { return snapshotState_; }

  bool popEvent(Event* out) {
    if (!eventReady_) return false;
    *out = pending_;
    eventReady_ = false;
    return true;
  }

  void emit(const Event& event) {
    pending_ = event;
    eventReady_ = true;
    StaticJsonDocument<128> doc;
    doc["op"] = event.op;
    if (event.slot >= 0) doc["slot"] = event.slot;
    if (strcmp(event.op, "page") == 0 || event.n != 0) doc["n"] = event.n;
    if (strcmp(event.op, "ptt") == 0) doc["on"] = event.pttOn;
    char buf[128];
    const size_t n = serializeJson(doc, buf, sizeof(buf));
    event_->setValue(reinterpret_cast<uint8_t*>(buf), n);
    if (connected_) event_->notify();
    Serial.print("EVT ");
    Serial.println(buf);
    Serial.printf("BLE event %s slot=%d n=%d\n", event.op, event.slot, event.n);
  }

  void setBattery(uint8_t) {}

  void tapRightCommand() {
    if (hidInput_ == nullptr) return;
    uint8_t down[8] = {0x80, 0, 0, 0, 0, 0, 0, 0};
    uint8_t up[8] = {};
    hidInput_->setValue(down, sizeof(down));
    hidInput_->notify();
    delay(80);
    hidInput_->setValue(up, sizeof(up));
    hidInput_->notify();
    Serial.println("HID RCmd tap");
  }

  void notifyAudio(const uint8_t* data, size_t length) {
    if (!audio_ || !connected_ || data == nullptr || length == 0) return;
    const size_t chunk = 120;
    for (size_t i = 0; i < length; i += chunk) {
      const size_t n = length - i < chunk ? length - i : chunk;
      audio_->setValue(const_cast<uint8_t*>(data + i), n);
      audio_->notify();
      delay(2);
    }
  }

 private:
  BLEServer* server_ = nullptr;
  BLEHIDDevice* hid_ = nullptr;
  BLECharacteristic* hidInput_ = nullptr;
  BLECharacteristic* snapshot_ = nullptr;
  BLECharacteristic* event_ = nullptr;
  BLECharacteristic* audio_ = nullptr;
  Snapshot snapshotState_{};
  Event pending_{};
  bool connected_ = false;
  bool dirty_ = false;
  bool eventReady_ = false;
  char hostLine_[768]{};
  size_t hostLineLen_ = 0;
};

}  // namespace watch_ble
