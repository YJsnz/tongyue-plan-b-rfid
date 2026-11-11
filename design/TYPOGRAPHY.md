# 应用字体规范

截图无法确认原始字体名称；本版按圆润笔端、饱满标题、深蓝文字和清晰正文匹配，使用 Resource Han Rounded CN（资源圆体）v0.990。

- 大标题、首页问候、横幅：Heavy，别名 `TongyueRoundedHeavy`。
- 分区标题、按钮、关键数字：Bold，别名 `TongyueRoundedBold`。
- 正文、辅助说明、输入框、未选中导航：Regular，别名 `TongyueRoundedRegular`。
- 主要文字颜色：`#152044`。
- 页面创建时通过 UIContext Font 注册，全部本地打包。

来源：https://github.com/CyanoHao/Resource-Han-Rounded/releases/tag/v0.990

原始字体文件位于 `entry/src/main/resources/rawfile/fonts/`，三个字重均保持原名及原始内容；随字体打包 SIL OFL 1.1 许可证。每个字重有 30,887 个字符映射，当前应用中文文案无缺字。
