# UE Camera Runtime

这是完整采集项目中的自研 UE4/UE5 相机运行时基础层，面向离线单机视觉数据采集。它借鉴“profile + 注入器 + 相机能力 ABI”的通用架构思想，但实现、协议和源码均由本项目维护，不复制或分发第三方闭源代码、资源或署名。

当前版本包含：

- 通用 `UeCameraInjector.exe`：按 PID、进程名或已登记 profile 自动选择目标进程；
- 通用 `UeCameraRuntime.dll`：加载后根据目标进程选择 profile；
- 离线 PE/签名扫描器：只读取游戏 EXE，不修改文件、不注入；
- `black-myth-wukong` 首个 profile，复用现有的 LWC `FMinimalViewInfo` 相机 ABI；
- 共享内存 pose、绝对 setPose、键鼠、HUD 和进程内 Hermite 轨迹协议；
- 上层继续复用项目的点位、22 方向静态扫描、OBS 和数据 manifest 管线。

## 使用

在 `games/black-myth-wukong/native/build_standalone_v1/Release/` 编译后：

```powershell
.\UeCameraInjector.exe --list
.\UeCameraInjector.exe --process b1-Win64-Shipping.exe
```

注入前必须退出游戏中的其他相机运行时或 Connector，且只能在离线单机环境使用。注入器发现已有相机运行时或冲突 Connector 时会停止，不会叠加 Hook。

## 增加游戏

1. 在 `profiles/` 增加一个 `ue_camera_profile_v1` JSON，先用离线扫描器验证签名数量；
2. 如果游戏的相机复制函数满足现有 ABI，只需把 profile 注册到 native registry；
3. 如果相机结构、寄存器契约或写回方式不同，新增一个明确命名的 ABI adapter；
4. 通过 pose 读取、绝对 setPose、轨迹画面和截图四项运行验收后，才把能力标记为可用。

UE5 不是统一的内存布局，因此“通用”表示共用运行时和 profile 接口，不表示任何 UE5 EXE 都能无配置注入。在线/反作弊游戏不属于本项目支持范围。
