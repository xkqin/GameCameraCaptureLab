# 媒体与数据传输脚本 / Media and transfer scripts

这些脚本是 RE9 历史数据维护工具，不属于统一采集 UI 的核心运行链。它们集中放在这里，避免占用仓库根目录；原有功能和文件名保持不变。

| 类别 | 文件 |
|---|---|
| HEVC/NVENC 转码 | `transcode_3000_hevc_nvenc.sh`、`transcode_4000_hevc_nvenc.sh`、`transcode_10000_hevc_nvenc.sh` |
| Ceph/rclone 上传 | `upload_4000_to_h_ceph.sh`、`upload_scene_1_2_10000_half_to_h_ceph.sh`、`upload_scene_1_3_3000_to_h_ceph.sh` |

转码脚本通过 `SOURCE_ROOT`、`OUTPUT_ROOT` 等环境变量工作；上传脚本通过 `SOURCE`、`DESTINATION` 和可选的 `ENDPOINT_HOST` 工作。日志、锁文件和进度文件默认写入输出目录或脚本目录，并且不会纳入 Git。

These are legacy RE9 dataset-maintenance tools rather than part of the unified capture UI. Run them from the repository root with a path such as `bash scripts/media/transcode_10000_hevc_nvenc.sh`; the existing environment-variable interface is unchanged.
