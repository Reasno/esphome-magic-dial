# Magic Dial 媒体锁屏落地说明

## 已修改文件
- `magic-dial.yaml`
- `magic_dial_media_aod.ha-helper.yaml`
- `magic_dial_media_artwork.py`
- `media_artwork_loader.h`

## 已备份文件
- `backups/20260912_magic_dial_media_aod/magic-dial.yaml.bak`

## HA 侧部署
1. 把 `magic_dial_media_aod.ha-helper.yaml` 合并到你的 `packages/` 或 `configuration.yaml`。
2. 把 `magic_dial_media_artwork.py` 复制到 HA 侧可执行的 Python 路径。
3. 安装 Pillow。
4. 按实际环境修改 helper 里的 `shell_command`：
   - Python 可执行路径
   - 脚本路径
   - `--token-file`
   - `--external-base-url`
5. 在 `/config/www/magic_dial_artwork/` 下准备：
   - `overrides.json`
   - 可选的 `overrides/` 图片目录

## 当前图片链路
- HA 将封面裁切为 360×360，并编码为 little-endian RGB565 原始像素文件。
- ESP32 常驻两块 259200 字节 PSRAM buffer，FreeRTOS worker 只下载到非活动 buffer。
- worker 使用 16 KiB HTTP Range 分块下载；单块失败只重试该块，全部 259200 字节
  完成且 generation 仍是最新请求时才发布结果。
- LVGL API 只在 ESPHome 主线程调用，主线程通过双 buffer 切换 image source。
- Wi-Fi 使用 `power_save_mode: none`；封面传输期间暂停 BLE 扫描，完成、失败或取消后
  由主线程恢复扫描，避免 Wi-Fi/BLE 共存导致长传输断流。

## ESPHome 侧部署
1. 先用当前仓库重新检查 `magic-dial.yaml`。
2. 编译通过后再 OTA / USB 刷写。
3. 首次部署时，先确认 HA 已能生成 `current.json` 与
   `cache/<媒体唯一键>.rgb565`（固定 259200 字节），再进 AOD 验证。

## 回滚
### 仅回滚 ESPHome
- 用 `backups/20260912_magic_dial_media_aod/magic-dial.yaml.bak` 覆盖 `magic-dial.yaml`。
- 重新编译并刷回设备。

### 回滚 HA
- 从 `packages:` 中移除 `magic_dial_media_aod.ha-helper.yaml`。
- 删除或停用 `magic_dial_media_artwork.py` 对应的调用。
- 删除 `/config/www/magic_dial_artwork/current.json` 与 `cache/` 下的
  `.rgb565` 缓存。

## 当前策略
- `HomePod`：优先，支持封面与进度。
- `PS4`：次优先，支持游戏名与封面。
- `Sony TV`：仅在不是 `Smart TV` 空壳态时参与；当前默认弱兜底。
