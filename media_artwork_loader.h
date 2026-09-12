#pragma once

#include <cstdio>
#include <new>
#include <string>

#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esphome/components/image/image.h"
#include "esphome/core/defines.h"
#include "esphome/core/log.h"
#ifdef USE_ESP32_BLE
#include "esphome/components/esp32_ble_tracker/esp32_ble_tracker.h"
#endif
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#if CONFIG_MBEDTLS_CERTIFICATE_BUNDLE
#include "esp_crt_bundle.h"
#endif

namespace magic_dial::media_artwork {

static constexpr size_t WIDTH = 360;
static constexpr size_t HEIGHT = 360;
static constexpr size_t BUFFER_BYTES = WIDTH * HEIGHT * 2;
static const char *const TAG = "media_artwork";

enum class PollResult : uint8_t {
  NONE,
  READY,
  ERROR,
};

class Loader {
 public:
  bool setup() {
    if (this->task_ != nullptr)
      return true;

    this->mutex_ = xSemaphoreCreateMutex();
    if (this->mutex_ == nullptr) {
      ESP_LOGE(TAG, "Failed to allocate loader mutex");
      return false;
    }

    for (size_t i = 0; i < 2; i++) {
      this->buffers_[i] = static_cast<uint8_t *>(heap_caps_malloc(BUFFER_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
      if (this->buffers_[i] == nullptr) {
        ESP_LOGE(TAG, "Failed to allocate PSRAM artwork buffer %u", static_cast<unsigned>(i));
        return false;
      }
      this->images_[i] = new (std::nothrow)
          esphome::image::Image(this->buffers_[i], WIDTH, HEIGHT, esphome::image::IMAGE_TYPE_RGB565,
                                esphome::image::TRANSPARENCY_OPAQUE);
      if (this->images_[i] == nullptr) {
        ESP_LOGE(TAG, "Failed to allocate artwork descriptor %u", static_cast<unsigned>(i));
        return false;
      }
    }

    if (xTaskCreatePinnedToCore(&Loader::task_entry_, "artwork_dl", 6144, this, 1, &this->task_, 0) != pdPASS) {
      this->task_ = nullptr;
      ESP_LOGE(TAG, "Failed to create artwork download task");
      return false;
    }
    ESP_LOGI(TAG, "Allocated two %u-byte RGB565 buffers in PSRAM", static_cast<unsigned>(BUFFER_BYTES));
    return true;
  }

  bool request(const std::string &url) {
    if (this->task_ == nullptr && !this->setup())
      return false;

    xSemaphoreTake(this->mutex_, portMAX_DELAY);
    if (url == this->active_url_) {
      this->requested_url_ = url;
      this->generation_++;
      this->result_ = PollResult::NONE;
      this->last_failed_ = false;
      xSemaphoreGive(this->mutex_);
      return true;
    }
    if (url == this->requested_url_ && !this->last_failed_) {
      xSemaphoreGive(this->mutex_);
      return false;
    }
    this->requested_url_ = url;
    this->generation_++;
    this->last_failed_ = false;
    this->result_ = PollResult::NONE;
    xSemaphoreGive(this->mutex_);
    this->pause_ble_scan_();
    xTaskNotifyGive(this->task_);
    return false;
  }

  void clear() {
    if (this->mutex_ == nullptr)
      return;
    xSemaphoreTake(this->mutex_, portMAX_DELAY);
    this->requested_url_.clear();
    this->active_url_.clear();
    this->generation_++;
    this->result_ = PollResult::NONE;
    this->last_failed_ = false;
    xSemaphoreGive(this->mutex_);
    this->resume_ble_scan_();
  }

  PollResult poll(esphome::image::Image **image) {
    if (this->mutex_ == nullptr)
      return PollResult::NONE;

    xSemaphoreTake(this->mutex_, portMAX_DELAY);
    const PollResult result = this->result_;
    if (result == PollResult::READY) {
      this->active_slot_ = this->ready_slot_;
      this->active_url_ = this->requested_url_;
      *image = this->images_[this->active_slot_];
    }
    this->result_ = PollResult::NONE;
    xSemaphoreGive(this->mutex_);
    if (result != PollResult::NONE)
      this->resume_ble_scan_();
    return result;
  }

 private:
  void pause_ble_scan_() {
#ifdef USE_ESP32_BLE
    if (this->ble_scan_paused_)
      return;
    auto *tracker = esphome::esp32_ble_tracker::global_esp32_ble_tracker;
    if (tracker == nullptr)
      return;
    tracker->set_scan_continuous(false);
    tracker->stop_scan();
    this->ble_scan_paused_ = true;
    ESP_LOGI(TAG, "Paused BLE scanning for artwork transfer");
#endif
  }

  void resume_ble_scan_() {
#ifdef USE_ESP32_BLE
    if (!this->ble_scan_paused_)
      return;
    auto *tracker = esphome::esp32_ble_tracker::global_esp32_ble_tracker;
    if (tracker != nullptr) {
      tracker->set_scan_continuous(true);
      tracker->start_scan();
      ESP_LOGI(TAG, "Resumed BLE scanning after artwork transfer");
    }
    this->ble_scan_paused_ = false;
#endif
  }

  bool is_current_(const std::string &url, uint32_t generation) {
    xSemaphoreTake(this->mutex_, portMAX_DELAY);
    const bool current = generation == this->generation_ && url == this->requested_url_;
    xSemaphoreGive(this->mutex_);
    return current;
  }

  bool wait_before_retry_(const std::string &url, uint32_t generation) {
    for (uint8_t step = 0; step < 20; step++) {
      if (!this->is_current_(url, generation))
        return false;
      vTaskDelay(pdMS_TO_TICKS(250));
    }
    return true;
  }

  bool download_(const std::string &url, uint8_t *buffer, uint32_t generation) {
    static constexpr size_t RANGE_BYTES = 16 * 1024;
    static constexpr uint8_t MAX_CONSECUTIVE_FAILURES = 12;
    static constexpr uint32_t TOTAL_TIMEOUT_MS = 180000;
    const TickType_t started_at = xTaskGetTickCount();
    size_t written = 0;
    uint8_t consecutive_failures = 0;
    uint16_t requests = 0;

    while (written < BUFFER_BYTES) {
      if (!this->is_current_(url, generation)) {
        ESP_LOGI(TAG, "Cancelled stale artwork download at %u/%u bytes", static_cast<unsigned>(written),
                 static_cast<unsigned>(BUFFER_BYTES));
        return false;
      }

      const uint32_t elapsed_ms = pdTICKS_TO_MS(xTaskGetTickCount() - started_at);
      if (elapsed_ms >= TOTAL_TIMEOUT_MS || consecutive_failures >= MAX_CONSECUTIVE_FAILURES)
        break;

      const size_t range_start = written;
      const size_t range_end =
          (range_start + RANGE_BYTES < BUFFER_BYTES ? range_start + RANGE_BYTES : BUFFER_BYTES) - 1;
      const size_t range_length = range_end - range_start + 1;

      esp_http_client_config_t config{};
      config.url = url.c_str();
      config.method = HTTP_METHOD_GET;
      config.timeout_ms = 3000;
      config.buffer_size = RANGE_BYTES;
      config.disable_auto_redirect = false;
      config.max_redirection_count = 3;
#if CONFIG_MBEDTLS_CERTIFICATE_BUNDLE
      config.crt_bundle_attach = esp_crt_bundle_attach;
#endif

      esp_http_client_handle_t client = esp_http_client_init(&config);
      bool chunk_complete = false;
      esp_err_t error = client == nullptr ? ESP_ERR_NO_MEM : ESP_OK;
      if (client != nullptr) {
        char range_header[40]{};
        std::snprintf(range_header, sizeof(range_header), "bytes=%u-%u", static_cast<unsigned>(range_start),
                      static_cast<unsigned>(range_end));
        esp_http_client_set_header(client, "Range", range_header);
        requests++;
        error = esp_http_client_open(client, 0);

        if (error == ESP_OK) {
          const int64_t content_length = esp_http_client_fetch_headers(client);
          const int status = esp_http_client_get_status_code(client);
          if (status == 206 && content_length == static_cast<int64_t>(range_length)) {
            size_t chunk_written = 0;
            while (chunk_written < range_length) {
              if (!this->is_current_(url, generation)) {
                ESP_LOGI(TAG, "Cancelled stale artwork download at %u/%u bytes",
                         static_cast<unsigned>(written), static_cast<unsigned>(BUFFER_BYTES));
                esp_http_client_close(client);
                esp_http_client_cleanup(client);
                return false;
              }
              const size_t remaining = range_length - chunk_written;
              const int read = esp_http_client_read(
                  client, reinterpret_cast<char *>(buffer + range_start + chunk_written), remaining);
              if (read <= 0) {
                error = read < 0 ? ESP_FAIL : ESP_ERR_INVALID_SIZE;
                break;
              }
              chunk_written += static_cast<size_t>(read);
            }
            chunk_complete =
                error == ESP_OK && chunk_written == range_length && esp_http_client_is_complete_data_received(client);
          } else {
            ESP_LOGW(TAG, "Unexpected range response: status=%d length=%lld expected=%u-%u", status,
                     static_cast<long long>(content_length), static_cast<unsigned>(range_start),
                     static_cast<unsigned>(range_end));
            error = ESP_ERR_INVALID_RESPONSE;
          }
        }

        esp_http_client_close(client);
        esp_http_client_cleanup(client);
      }

      if (chunk_complete) {
        written += range_length;
        consecutive_failures = 0;
        if (written == BUFFER_BYTES || written % (64 * 1024) == 0) {
          ESP_LOGI(TAG, "Artwork download progress: %u/%u bytes", static_cast<unsigned>(written),
                   static_cast<unsigned>(BUFFER_BYTES));
        }
        vTaskDelay(pdMS_TO_TICKS(1));
        continue;
      }

      consecutive_failures++;
      ESP_LOGW(TAG, "Range %u-%u failed (%s), retry %u/%u", static_cast<unsigned>(range_start),
               static_cast<unsigned>(range_end), esp_err_to_name(error),
               static_cast<unsigned>(consecutive_failures), static_cast<unsigned>(MAX_CONSECUTIVE_FAILURES));
      if (!this->wait_before_retry_(url, generation))
        return false;
    }

    const uint32_t elapsed_ms = pdTICKS_TO_MS(xTaskGetTickCount() - started_at);
    if (written != BUFFER_BYTES) {
      ESP_LOGW(TAG, "Artwork download incomplete at %u/%u bytes after %u ms and %u requests",
               static_cast<unsigned>(written), static_cast<unsigned>(BUFFER_BYTES),
               static_cast<unsigned>(elapsed_ms), static_cast<unsigned>(requests));
      return false;
    }
    ESP_LOGI(TAG, "Downloaded %u-byte RGB565 artwork in %u ms using %u ranged requests",
             static_cast<unsigned>(written), static_cast<unsigned>(elapsed_ms), static_cast<unsigned>(requests));
    return true;
  }

  static void task_entry_(void *parameter) {
    static_cast<Loader *>(parameter)->task_loop_();
  }

  void task_loop_() {
    while (true) {
      ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

      std::string url;
      uint32_t generation;
      uint8_t target_slot;
      xSemaphoreTake(this->mutex_, portMAX_DELAY);
      url = this->requested_url_;
      generation = this->generation_;
      target_slot = this->active_slot_ ^ 1;
      xSemaphoreGive(this->mutex_);
      if (url.empty())
        continue;

      ESP_LOGI(TAG, "Downloading RGB565 artwork into PSRAM buffer %u", target_slot);
      const bool ok = this->download_(url, this->buffers_[target_slot], generation);

      xSemaphoreTake(this->mutex_, portMAX_DELAY);
      if (generation == this->generation_ && url == this->requested_url_) {
        this->ready_slot_ = target_slot;
        this->last_failed_ = !ok;
        this->result_ = ok ? PollResult::READY : PollResult::ERROR;
      }
      xSemaphoreGive(this->mutex_);
    }
  }

  uint8_t *buffers_[2]{nullptr, nullptr};
  esphome::image::Image *images_[2]{nullptr, nullptr};
  SemaphoreHandle_t mutex_{nullptr};
  TaskHandle_t task_{nullptr};
  std::string requested_url_;
  std::string active_url_;
  uint32_t generation_{0};
  uint8_t active_slot_{0};
  uint8_t ready_slot_{0};
  PollResult result_{PollResult::NONE};
  bool last_failed_{false};
  bool ble_scan_paused_{false};
};

static Loader loader;

}  // namespace magic_dial::media_artwork
