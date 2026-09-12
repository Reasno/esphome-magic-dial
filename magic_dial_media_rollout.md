# Magic Dial 媒体锁屏落地说明

## 已修改文件
- `magic-dial.yaml`
- `magic_dial_media_aod.ha-helper.yaml`
- `magic_dial_media_artwork.py`

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

## ESPHome 侧部署
1. 先用当前仓库重新检查 `magic-dial.yaml`。
2. 编译通过后再 OTA / USB 刷写。
3. 首次部署时，先确认 HA 已能生成 `current.json` 与 `current.png`，再进 AOD 验证。

## 回滚
### 仅回滚 ESPHome
- 用 `backups/20260912_magic_dial_media_aod/magic-dial.yaml.bak` 覆盖 `magic-dial.yaml`。
- 重新编译并刷回设备。

### 回滚 HA
- 从 `packages:` 中移除 `magic_dial_media_aod.ha-helper.yaml`。
- 删除或停用 `magic_dial_media_artwork.py` 对应的调用。
- 删除 `/config/www/magic_dial_artwork/current.json` 与 `current.png`。

## 当前策略
- `HomePod`：优先，支持封面与进度。
- `PS4`：次优先，支持游戏名与封面。
- `Sony TV`：仅在不是 `Smart TV` 空壳态时参与；当前默认弱兜底。
