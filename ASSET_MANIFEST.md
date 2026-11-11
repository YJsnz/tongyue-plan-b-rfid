# 运行时美术资源

本文只记录当前 HarmonyOS 项目实际使用的资源。页面在 ArkUI 中绘制文字和交互控件，插画以独立图像资源打包。

## 首页与角色

- `home_wave_body.png`、`home_wave_arm.png`：首页女孩身体与手臂的透明分层图。两层常驻，不切换帧；手臂围绕肩部以原生旋转完成约 2.34 秒挥手，之后待机约 10 秒。
- `home_garden.svg`、`home_reading_banner.jpg`、`home_recommend.svg`、`home_books.svg`、`home_checkin.svg`、`home_chat.svg`：首页场景装饰、共读横幅与快捷入口。
- `motion_reading_boy.png`：示意图最左侧绿衣卷发男孩，手持蓝色绘本；绘本馆卡片入场时轻移位、缩放与淡入。
- `motion_reward_boy.png`：示意图蓝衣棕发男孩抱黄色星星；完成共读时轻弹一次。
- `motion_thinking_child.png`：示意图黄衣紫背带、黄色发圈女孩；AI 等待回答时歪头。
- `motion_happy_child.png`：示意图绿衣黄裙女孩举手庆祝；RFID 配对成功时跳跃一次。
- 上述四张插画分别以原示意图对应人物为参照生成，使用预加载固定图层配合 ArkUI 原生位移、缩放与旋转，不逐帧替换图片。动作触发后回到静态，等待下一次交互。
- `rig_reading.png`、`rig_reward.png`、`rig_thinking.png`、`rig_happy.png`：在上述四张插画基础上生成的双格分层图集。身体与头部、星星双手、双臂分别常驻；`ReferenceCompanion` 控制活动部位的连续旋转与位移，播放后待机 10 秒。开启减少动态效果时使用原静态插画。
- 首页挥手继续使用 `home_wave_body.png`、`home_wave_arm.png` 两层骨骼式原生旋转；绘本卡片采用错峰淡入和轻微上移。页面之间统一使用短暂淡出、轻移位、淡入过渡，并尊重“减少动态效果”设置。
- `girl_wave_00..07.png`、`girl_reading_00..07.png`、`girl_think_00..07.png`、`girl_celebrate_00..07.png`：早期动作序列资源，当前页面不再引用。

## 五个面板

- `page_library.jpg`、`page_covers_forest.jpg`、`page_covers_moon.jpg`、`page_covers_seed.jpg`、`page_themes_spring.jpg`、`page_themes_feelings.jpg`、`page_themes_nature.jpg`：绘本馆场景、封面和主题卡片。
- `page_boy.png`、`page_reader.png`：阅读成长和设备中心插画。
- `page_sky.jpg`：AI 共读的回答配图。
- `page_radar.svg`：阅读能力图示底图。
- `page_content.svg`、`page_focus.svg`、`page_settings.svg`、`icon_battery.svg`、`icon_voice.svg`：设备功能和状态图标。
- `nav_home.svg`、`nav_book.svg`、`nav_growth.svg`、`nav_ai.svg`、`nav_device.svg`：底部导航图标。
- `AppScope/resources/base/media/app_icon.svg`：应用图标。

## 字体

应用将 Resource Han Rounded CN v0.990 的 Regular、Bold、Heavy 字重注册为 `TongyueRoundedRegular`、`TongyueRoundedBold`、`TongyueRoundedHeavy`。字体文件和 SIL Open Font License 1.1 随应用放在 `entry/src/main/resources/rawfile/fonts/`。

上游字体项目与许可证：<https://github.com/CyanoHao/Resource-Han-Rounded/releases/tag/v0.990>
