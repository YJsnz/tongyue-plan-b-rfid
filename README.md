# 童阅智伴 · HarmonyOS 家长端

面向 HarmonyOS 的 ArkTS / ArkUI 家长端交互原型，优先呈现 Hi3516 + RFID 阅读器方案。应用包含首页、绘本馆、阅读成长、AI 共读和设备中心五个面板，使用原创绘本插画、圆体中文字体与角色动作，适配手机竖屏。

![五个主面板预览](docs/app-overview.jpg)

## 运行

1. 使用 DevEco Studio 打开本仓库目录。
2. 安装 HarmonyOS SDK API 26（兼容版本与目标版本均为 26.0.0）。
3. 选择 `entry` 模块和 `default` 产品后，在 IDE 中构建并运行。

也可在项目目录使用 DevEco Studio 自带的 Hvigor 工具构建：

```text
hvigorw --mode module -p product=default -p module=entry@default assembleHap
```

项目未包含签名密钥；本地可构建 unsigned HAP，发布或安装到受限设备前需在 DevEco Studio 配置签名。

## 交互范围

- 首页欢迎、阅读任务和继续共读入口；女孩挥手由常驻身体与手臂图层组成，使用原生旋转动作，完成后休息约 10 秒。点击角色可重播，支持减少动态效果。
- 绘本馆提供搜索、分类与年龄筛选、绘本卡片和主题入口。
- 阅读成长提供周数据、阅读趋势、能力图示与徽章面板。
- AI 共读展示预设示例问答、推荐问题和对话记录；输入内容使用本地演示回复。
- 设备中心提供 RFID 阅读器状态、配对引导、内容包、专注模式、音量和阅读时长等演示控件。

设备连接、RFID 扫描、语音输入、AI 问答、阅读统计与设置均为本地演示状态，不连接真实设备或服务，也不上传音频；应用重启后状态不会保留。

## 目录

- `AppScope/`：应用图标与全局配置。
- `entry/`：ArkTS 页面、组件、模块配置和运行资源。
- `docs/`：界面预览。
- `ASSET_MANIFEST.md`：运行资源与字体许可说明。
- `DUOLINGO_RESEARCH.md`：公开界面与角色设计资料及本项目采用的原则。
