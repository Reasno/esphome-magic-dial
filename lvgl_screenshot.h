#pragma once

#include <algorithm>
#include <cstdio>
#include <cstring>

#include "esp_heap_caps.h"
#include "esphome/components/mqtt/mqtt_client.h"
#include "esphome/core/log.h"
#include "lvgl.h"
#include "src/draw/snapshot/lv_snapshot.h"

namespace magic_dial::screenshot {

static constexpr uint32_t WIDTH = 360;
static constexpr uint32_t HEIGHT = 360;
static constexpr size_t BYTES = WIDTH * HEIGHT * 2;
static constexpr size_t CHUNK_BYTES = 1024;
static const char *const TAG = "lvgl_screenshot";

class Publisher {
 public:
  bool capture(lv_obj_t *root) {
    if (root == nullptr || this->publishing_) {
      ESP_LOGW(TAG, "Screenshot request rejected: root=%p publishing=%s", root, this->publishing_ ? "yes" : "no");
      return false;
    }
    if (this->buffer_ == nullptr) {
      this->buffer_ = static_cast<uint8_t *>(
          heap_caps_aligned_alloc(LV_DRAW_BUF_ALIGN, BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
      if (this->buffer_ == nullptr) {
        ESP_LOGE(TAG, "Unable to allocate %u-byte screenshot buffer", static_cast<unsigned>(BYTES));
        return false;
      }
    }

    lv_draw_buf_init(&this->draw_buf_, WIDTH, HEIGHT, LV_COLOR_FORMAT_RGB565, 0, this->buffer_, BYTES);
    lv_draw_buf_set_flag(&this->draw_buf_, LV_IMAGE_FLAGS_MODIFIABLE);
    if (lv_snapshot_take_to_draw_buf(root, LV_COLOR_FORMAT_RGB565, &this->draw_buf_) != LV_RESULT_OK) {
      ESP_LOGE(TAG, "LVGL snapshot failed");
      return false;
    }

    this->capture_id_ = millis();
    this->chunk_index_ = 0;
    this->chunk_count_ = (BYTES + CHUNK_BYTES - 1) / CHUNK_BYTES;
    this->crc32_value_ = calculate_crc32_(this->buffer_, BYTES);
    this->meta_pending_ = true;
    this->publishing_ = true;
    ESP_LOGI(TAG, "Captured %ux%u RGB565 frame id=%u crc32=%08x", static_cast<unsigned>(WIDTH), static_cast<unsigned>(HEIGHT),
             static_cast<unsigned>(this->capture_id_), static_cast<unsigned>(this->crc32_value_));
    return true;
  }

  void publish_next(esphome::mqtt::MQTTClientComponent *mqtt) {
    if (!this->publishing_ || mqtt == nullptr || !mqtt->is_connected())
      return;

    char topic[128]{};
    if (this->meta_pending_) {
      char payload[192]{};
      std::snprintf(payload, sizeof(payload),
                    "{\"id\":%u,\"width\":%u,\"height\":%u,\"format\":\"rgb565le\","
                    "\"bytes\":%u,\"chunk_bytes\":%u,\"chunks\":%u,\"crc32\":\"%08x\"}",
                    static_cast<unsigned>(this->capture_id_), static_cast<unsigned>(WIDTH), static_cast<unsigned>(HEIGHT), static_cast<unsigned>(BYTES),
                    static_cast<unsigned>(CHUNK_BYTES), static_cast<unsigned>(this->chunk_count_),
                    static_cast<unsigned>(this->crc32_value_));
      if (mqtt->publish("magic_dial/debug/screenshot/meta", payload, std::strlen(payload), 0, false))
        this->meta_pending_ = false;
      return;
    }

    if (this->chunk_index_ < this->chunk_count_) {
      const size_t offset = this->chunk_index_ * CHUNK_BYTES;
      const size_t length = std::min(CHUNK_BYTES, BYTES - offset);
      std::snprintf(topic, sizeof(topic), "magic_dial/debug/screenshot/%u/chunk/%u",
                    static_cast<unsigned>(this->capture_id_), static_cast<unsigned>(this->chunk_index_));
      if (mqtt->publish(topic, reinterpret_cast<const char *>(this->buffer_ + offset), length, 0, false))
        this->chunk_index_++;
      return;
    }

    std::snprintf(topic, sizeof(topic), "magic_dial/debug/screenshot/%u/done",
                  static_cast<unsigned>(this->capture_id_));
    if (mqtt->publish(topic, "1", 1, 0, false)) {
      ESP_LOGI(TAG, "Published screenshot id=%u in %u chunks", static_cast<unsigned>(this->capture_id_),
               static_cast<unsigned>(this->chunk_count_));
      this->publishing_ = false;
    }
  }

 private:
  static uint32_t calculate_crc32_(const uint8_t *data, size_t length) {
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < length; i++) {
      crc ^= data[i];
      for (uint8_t bit = 0; bit < 8; bit++)
        crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
    return ~crc;
  }

  uint8_t *buffer_{nullptr};
  lv_draw_buf_t draw_buf_{};
  uint32_t capture_id_{0};
  uint32_t crc32_value_{0};
  size_t chunk_index_{0};
  size_t chunk_count_{0};
  bool meta_pending_{false};
  bool publishing_{false};
};

static Publisher publisher;

}  // namespace magic_dial::screenshot
