# 示意图四处角色动效

使用内置 image_gen 工具，以用户提供的四角色横条示意图为人物参照，分别生成透明 PNG。保留各自发型、服装与半身构图；固定图层预加载，以连续属性动画播放。

## 生成提示词摘要

共同要求：Use the attached narrow illustration strip as the ONLY visual reference. Faithfully reconstruct ONE of its four original cartoon characters as a crisp transparent PNG app asset. Match the exact reference face, hairstyle, costume, proportions, soft shaded glossy children's-book rendering and colors. Keep the upper-body/bust framing as in the reference. No UI, phones, landscape, grass, labels, arrows, text, watermark or backdrop. Preserve true transparency with generous padding.

- motion_reading_boy.png：Far-left reading boy, fluffy curly black hair, green long sleeves, holding an open blue picture book, excited expression.
- motion_reward_boy.png：Second reward boy, short chestnut swept hair, blue shirt, green lower clothing, closed smiling eyes, hugging one large yellow five-point star.
- motion_thinking_child.png：Third thinking girl, dark side pigtails with yellow ties, yellow shirt and purple dungarees, finger to cheek, tilted head, yellow question mark.
- motion_happy_child.png：Far-right happy girl, chestnut hair with yellow tie, green top and yellow skirt, eyes closed joyfully, both arms up, jumping.

## 播放与触发

- 绘本馆：绿衣读书男孩和卡片错峰入场，单次约 480ms。
- 共读奖励：蓝衣男孩与星星、双手独立分层，星星轻摇与上移，单次约 2340ms。
- AI 等待：黄紫衣女孩头部与身体独立分层，歪头、停顿、回正，单次约 2200ms，随后显示回答。
- RFID 配对成功：绿衣黄裙女孩双臂与身体独立分层，抬手、起跳、落地，单次约 1800ms。
- 页面切换：130ms 淡出，240ms 淡入与轻位移；快速导航使用代次标识取消过期回调。
- 开启减少动态效果时呈现原静态角色；各动作播放一次后待机 10 秒，点击可以提前重播，动作中点击不会打断动作。

## 分层资源与预览

`rig_reading.png`、`rig_reward.png`、`rig_thinking.png`、`rig_happy.png` 是使用内置 image_gen 编辑原角色得到的双格透明图集。左格保留静止身体，右格保留活动部位；应用以两个常驻视口读取，不逐帧换纹理。读书男孩活动头部，抱星男孩活动星星和双手，思考女孩活动头发、脸和手，开心女孩分别活动双臂。

分层提示词：Edit this exact existing character image into a two-cell animation rig atlas. Preserve identity, face, costume, proportions and rendering. Wide 2:1 transparent PNG, two equal square cells. Left: stationary base. Right: detached moving part in its original coordinate system. Slight overlap at joints. No text, backgrounds, glow, ghost copies or reposing.

`ReferenceCompanion.ets` 负责关节对齐、连续补间、10 秒待机、点击重播和生命周期回调取消。`design/MOTION_PREVIEW.html` 可以直接打开，含四个角色、暂停与重播控件，复用相同的动作时长和图集对齐参数。

