# Hi3516 + RFID 首轮联调

首轮目标是在一块实际开发板和一台鸿蒙手机上完成：连接阅读器 → 给一本绘本绑定标签 → 再次刷卡识别该书 → 在板端完成一段共读 → 手机显示同一条阅读记录。使用一本自制或已获授权的绘本、一张已知标签和一个测试档案。

方案 B 的 RFID 标签用于确定书籍，阅读位置由手动选段或后续语音对齐提供。首轮用板端按键选段和预置音频完成闭环，给后续 B1 朗读定位保留 `segmentId` 和 `positionSource`。板端在手机离线时仍能读卡、播音和记录。

## 实施顺序与分工

| 顺序 | 工作 | 负责人角色 | 完成标志 |
| --- | --- | --- | --- |
| 1 | 确认实际板卡型号、系统镜像/SDK、读卡模块型号、实际总线与接线。准备一张已知标签和一张未绑定标签。 | 板端 | 留下板卡配置和串口日志。单次贴卡能读到完整 UID，移开再贴仍得到同一个 UID。 |
| 2 | 实现贴卡事件、UID 到绘本的持久化绑定、StoryPack 装载、预置音频播放、按键选段和会话结束。用短时录音验证板端输入。 | 板端、内容 | 重启后识别同一本书，脱离手机仍能播音并留下阅读摘要。 |
| 3 | 在同一局域网接入设备状态、扫描与绑定、阅读摘要。联调时先手动填设备地址。 | 板端、客户端 | 手机显示板端状态。绑定成功来自设备确认。断开重连后仍能同步记录。 |
| 4 | 将设备中心和绘本配对页接入数据，保存连接配置、绑定结果和已同步的摘要。把阅读成长的阅读时长和次数改为从真实记录汇总。 | 客户端 | 真机重启后能查看已有记录。设备不可达、扫描超时、标签冲突及知识包缺失均有可操作提示。 |
| 5 | 用一段固定录音验证板端音频处理，再把手动选段作为基线，研发句段级朗读对齐与位置相关 FAQ。 | 算法、板端 | 记录实际板卡的实时率、内存和错位情况。方案 B 原文的指标作为研发目标，结合实测决定何时接入。 |

客户端设备中心通过「管理阅读器与绘本标签」进入联调页。连接和绑定提示来自接口响应，阅读记录和游标一起保存到本机。首页和成长页使用当前阅读器的同一份已同步摘要。统计按手机时区、周一至周日计算，重复同步不增加次数，日期未知的记录可在设备中心查看。阅读器记录当前按设备归集，尚无孩子档案字段。

## 联调接口草案

设备配对解决手机能否访问阅读器，绘本配对解决标签对应哪一本书。板端开放局域网接口时使用 HTTPS，并要求每个请求带 `Authorization: Bearer <配对密钥>`。仅本机的调试入口可用 HTTP。

| 操作 | 建议接口 | 数据与行为 |
| --- | --- | --- |
| 读取设备状态 | `GET /v1/device` | 返回 `deviceId`、`firmwareVersion`、`protocolVersion`。手机根据请求是否成功及最近更新时间判断在线状态。板端有可靠电量来源时可返回 `batteryPercent`。 |
| 读取绘本清单 | `GET /v1/books` | 返回 `items`，每项包含 `bookId` 和 `title`。应用用板端绘本清单生成选择项，板端在绑定和刷卡时校验实际故事包。 |
| 导入绘本包 | `POST /v1/storypacks` | `Content-Type: application/gzip`，请求体是一个 `.tar.gz` 绘本包，最多 24 MiB。验证全部文件后发布，返回 `bookId`、`title`、`version`、`alreadyInstalled`。局域网需要相同的 HTTPS 与配对密钥。 |
| 开始一次绘本配对扫描 | `POST /v1/scans` | 返回 `scanId`。只接受创建后新刷到的标签。扫描期间暂缓正常刷卡开书，结束或超时后恢复。 |
| 查询扫描结果 | `GET /v1/scans/{scanId}` | 返回 `waiting`、`detected` 或 `expired`。检测成功时返回 `tagUid`，以保留前导零的完整大写十六进制字符串传输。 |
| 绑定标签 | `PUT /v1/book-bindings/{tagUid}` | 请求包含 `scanId`、`bookId`。板端校验扫描和 UID 对应、知识包可用后持久化。重复提交同一个 UID 与绘本返回成功。若 UID 已属于另一本书，返回冲突并保留原绑定。 |
| 拉取已结束会话 | `GET /v1/reading-sessions?after={cursor}&limit=8` | 返回按板端持久化顺序排列的 `items`、不透明的 `nextCursor` 和 `hasMore`。`limit` 支持 1–8，旧请求可以省略。手机按 `(deviceId, sessionId)` 更新记录，全部页面成功保存后再推进游标。 |
| 拉取位置复核 | `GET /v1/position-reviews` | 返回此阅读器上最多 512 条家长复核。手机只在家长点击「同步阅读记录」时拉取并合并。 |
| 保存位置复核 | `PUT /v1/position-reviews/{sessionId}/{eventId}` | 请求包含 `status`、`note` 和 `updatedAtMs`。板端验证会话和轨迹点确实存在，状态只能为 `needs_review`、`position_mismatch` 或 `confirmed`，备注最多 256 字。每次修改保留一条最新记录；时间较旧或相同的写入返回板端已有版本，不覆盖它。 |

接口返回可区分的错误码，至少包括 `SCAN_EXPIRED`、`TAG_ALREADY_BOUND`、`PACK_NOT_FOUND`、`PACK_INVALID`。客户端请求超时后保留上次已确认的绑定或历史数据，并允许重试。解绑和重新分配标签需要单独确认，首轮通过板端调试操作恢复测试环境。

板端状态文件应放在本地持久文件系统中，并由单个阅读器进程持有。保存时先写入并同步同目录临时文件，再原子替换状态文件。在 Linux 上还会同步父目录，成功后才确认绑定或结束会话；位置复核也使用相同的原子保存路径。替换前的错误返回 `STORAGE_UNAVAILABLE`，可在修复存储后重试。若替换后的目录同步失败，返回 `STORAGE_RESTART_REQUIRED`，检查文件系统后重启阅读器并核对原记录。现有 JSON 状态直接读取，损坏的文件不会被自动覆盖。[Linux `fsync` 说明](https://www.man7.org/linux/man-pages/man2/fsync.2.html)

最小会话字段为 `sessionId`、`bookId`、`packVersion`、`endedAtMs`、`durationMs`、`lastSegmentId`、`positionSource`。`sessionId` 在板端持久化且重启后不复用。时长用单调时钟计算。板端没有可靠的系统时钟时，加 `--no-trusted-clock`，让 `endedAtMs` 返回 `null`。手机保存并单独显示同步时间，未知阅读日期的记录不计入日历统计。手动选段用 `positionSource: manual`，自动定位用 `audio`。同步数据只包含摘要，原始音频默认不保存、不传输。

新会话可带 `trajectory`，最多保留最近 128 个定位点；超出的数量记入 `trajectoryDropped`。每个定位点含唯一 `eventId`、相对会话开始的 `offsetMs`、`segmentId`、定位来源和状态。来源为 `manual` 时状态是 `selected`；MFCC/CTC 可记录 `matched`、`repeated`、`relocated` 或 `uncertain`。CTC 点还可带 StoryPack 原词界上的词索引、字符偏移和词文本。`traceStats` 保存重读、重定位、不确定计数，`positionUncertain` 和 `lastWordIndex` 表示会话结束时的位置状态。板端目前记录句段级 MFCC 轨迹，词位级 CTC 轨迹由电脑模拟器产生。家长端复核按设备、会话和定位点关联，独立于原始轨迹；原始轨迹不会被家长标注改写。复核先保存在手机，只有家长点击「同步阅读记录」时才发送到当前阅读器，供另一台已配对的家长端查看。状态文件最多保留 512 条，每条可含不超过 256 字的家长备注；不上传原始录音或转写文本。

会话可附带 `answers`，最多保留最后 20 条已播完的答案。每条含 `segmentId`、已审核的 `question`、`answer`、`evidence`、`source` 和播完时距会话开始的 `offsetMs`。板端在播放器正常退出后记入证据。未知问法、原始语音、用户自由输入和音频文件路径不进入摘要。旧会话可以没有 `answers`。

首轮可预装一个最小 StoryPack，包含书籍 ID、书名、版本、稳定段落 ID 和与段落关联的预置音频。板端按 `bookId` 找包并在读取前校验清单及必需文件。家长端的绘本包页面也可选择本地包导入阅读器；手机操作仍待真机验收。

## 真机验收

| 场景 | 通过条件 |
| --- | --- |
| 上板 G0 | 板端在断公网时能读取 UID、采集音频并播放预置音频。记录实际镜像版本、读卡模块、总线与串口输出。 |
| 局域网设备配对 | 手机可用正确的 HTTPS 地址、可信证书和密钥连接。缺少或输错密钥时无法读取绘本或阅读记录。手机重启后可用系统保管的密钥重新连接。 |
| 绘本配对 | 扫描开始前留下的读卡结果不会触发成功。贴入已知标签后手机显示读到的绘本绑定结果，设备重启后绑定仍有效。 |
| 手机导入绘本 | 在绘本包页面连接阅读器，选择一个有效包。导入后显示书名和版本，可返回标签配对使用。重复导入同一包成功；不同版本冲突、损坏包和中断传输保留旧书。关闭文件选择器后可以再次导入。 |
| 正常共读 | 移开并再次贴卡，设备载入正确的书并播音。标签留在感应区时只开始一次会话。手动切换段落后结束，会话时长和末段可在手机查看。 |
| 持续采音和按键 | 首段播完才开始定位，播音中不会推进位置。重复朗读保持原段，跳读可重定位。按键短按只执行一次，自动重复不触发。按键纠正后，摘要记录最后确认的段落和来源。 |
| 离线语音问答 | 按下提问键时暂停跟读。已录入的问法在正确段落中播报有证据的预置答案。低置信位置、未知问法和跨段问题触发对应提示。问答后恢复跟读，问题语音不影响阅读位置。 |
| 家长查看问答证据 | 等答案播完后结束会话，同步并在设备中心展开该记录。核对问题、答案和原文证据来自播报时的段落。播放中途结束的答案不应出现。 |
| 未知标签与内容不可用 | 未绑定标签可进入配对引导。知识包缺失或损坏时给出明确状态。两种情况均不播放其他绘本或生成成功记录。 |
| 扫描超时 | 启动扫描后不贴卡，手机显示超时并可重新开始。阅读器恢复正常刷卡开书。 |
| 绑定冲突 | 临时预装第二本测试绘本。同一标签试图绑定第二本书时，手机显示冲突，原书仍可正常识别。重复提交原绑定仍成功。 |
| 手机断连 | 手机离开局域网后在板端完成共读。重新连接后同步一次，重复刷新不增加记录或阅读次数。 |
| 手机重启 | 已保存的历史仍可查看。设备仍离线时界面显示离线，已同步的记录仍可访问。 |
| 板端存储恢复 | 绑定成功并保存一条摘要后重启。核对设备 ID、标签和摘要仍在，摘要只出现一次。在受控实验环境中补做断电验证，记录板端文件系统和供电方式。 |

首轮联调的硬件交接信息为实际板卡型号、系统镜像/SDK 版本、RFID 模块丝印型号、总线及接线图。如果手头只有板卡和模块，先拍清楚丝印并记录接线，再做步骤 1 的 UID 串口验收。

## 开发板到手前在 Windows 上联调

在客户端仓库根目录运行。模拟器只监听本机，需要 Python 3，无第三方依赖。绑定与已结束的阅读会话保存在指定 JSON 文件，使用同一个文件重启可验证持久化。换一个文件名可从空设备开始。

```powershell
python .\tools\rfid_simulator.py --state "$env:TEMP\tongyue-rfid-sim.json"
```

在另一个 PowerShell 窗口执行以下流程。`/sim/` 接口模拟刷卡和结束共读，`/v1/` 接口供客户端联调。默认扫描窗口为 30 秒。

```powershell
$reader = 'http://127.0.0.1:8765'
Invoke-RestMethod "$reader/v1/device"
$scan = Invoke-RestMethod "$reader/v1/scans" -Method Post
$tag = @{ tagUid = '0004A12B' } | ConvertTo-Json
Invoke-RestMethod "$reader/sim/tap" -Method Post -ContentType 'application/json' -Body $tag
Invoke-RestMethod "$reader/v1/scans/$($scan.scanId)"
$binding = @{ scanId = $scan.scanId; bookId = 'bear-friends' } | ConvertTo-Json
Invoke-RestMethod "$reader/v1/book-bindings/0004A12B" -Method Put -ContentType 'application/json' -Body $binding
Invoke-RestMethod "$reader/sim/tap" -Method Post -ContentType 'application/json' -Body $tag
Invoke-RestMethod "$reader/sim/finish" -Method Post -ContentType 'application/json' -Body '{"lastSegmentId":"s2"}'
$batch = Invoke-RestMethod "$reader/v1/reading-sessions"
$batch.items
Invoke-RestMethod "$reader/v1/reading-sessions?after=$($batch.nextCursor)"
```

预期首次读取返回一条记录，带游标再次读取返回空 `items`。`bear-friends` 和 `forest-seed` 是模拟器中的两本测试绘本，`s1` 和 `s2` 是模拟段落。RFID 读卡硬件、板端音频和真实 StoryPack 文件仍需单独联调。

### 用鸿蒙手机或模拟器接入

1. 在 DevEco Studio 启动已配置签名的应用，或在可用的鸿蒙模拟器上运行。用 `hdc list targets` 确认只有当前联调目标，再执行 `hdc rport tcp:8765 tcp:8765`。这会将目标上的本地端口映射到电脑上的模拟器。
2. 保持电脑上的模拟器运行。在应用中打开「设备 → 管理阅读器与绘本标签」，填写 `http://127.0.0.1:8765` 并连接。
3. 在应用选择绘本并启动扫描。电脑端执行上方第一条 `/sim/tap` 命令，等待应用显示 UID，再在应用确认绑定。
4. 电脑端再次调用 `/sim/tap`，再调用 `/sim/finish`。回到应用同步记录，检查出现一条对应绘本的记录。再次同步应无新增。退出应用、关闭电脑模拟器后重新进入应用，检查已同步的记录仍可查看。
5. 返回首页，核对最近绘本、末段和今日次数。打开成长页切换本周和上周，核对同一条记录只出现在对应日期和周，切换阅读器后检查统计按设备区分。

### 编译 StoryPack 并验证按段落检索

在内容编译电脑的 Python 环境先安装 `python -m pip install -r tools/requirements-authoring.txt`。该依赖只用于离线生成拼音索引；读取已有包和运行阅读器不需要它。新包的二进制布局、作者词界和专名候选见 [StoryPack 索引格式](STORYPACK_FORMAT.md)。

复制 `tools/examples/bear-friends.json` 到工作目录。根据 `segments[].text` 和 `faq[].answer` 录制 `s1.wav`、`s2.wav`、`a1.wav`、`a2.wav`，放在 JSON 同目录。录音要求为 16 kHz 单声道 PCM16 WAV。示例 JSON 只包含原创文字，音频由项目组录制。

```powershell
python .\tools\storypack.py <样本目录>\bear-friends.json "$env:TEMP\tongyue-packs"
python .\tools\rfid_simulator.py --state "$env:TEMP\tongyue-pack-reader.json" --packs "$env:TEMP\tongyue-packs"
```

在电脑端按前面的步骤绑定并再次刷卡。模拟器会加载清单、校验文件哈希并返回首段正文和音频路径。使用 `POST /sim/select-segment` 传入 `{"segmentId":"s2"}` 模拟板端按键选段，再用 `POST /sim/ask` 传入 `{"question":"它为什么把伞给小兔？"}`。只有当前段落的已审核问法才能取得答案、原文证据和预置音频路径。问到其它段落或未知问题时返回 `unsupported`。调用 `POST /sim/finish` 时可省略 `lastSegmentId`，使用已选的当前段落。

这个格式使用 `book.json`、`segments.json`、`faq_index.sqlite` 和 `audio_cache/`。新包还生成 `segments.bin`、`phoneme_lexicon.bin` 和 `policy.json`，用于后续细粒度对齐。编译时给 `storypack.py` 传入 `--locator <电脑端 locate_wav v2或更新版本>`，还可生成 `locator/segments.mfcc` 和 `locator/questions.mfcc`，保存模板路线的参考声学特征。全部文件列入哈希清单。当前实际定位仍为 MFCC/DTW 句段匹配；文本音节索引与声学词位跟踪的实现状态见 [方案对照](PLAN_B_STATUS.md)。

### 使用现成的中文联调绘本

从 Windows 项目根目录运行下面的命令。生成器需要本机可用的 Windows 中文语音，目标目录必须是新目录。

```powershell
python -B .\tools\make_demo_storypack.py .\output\rfid-demo
```

要生成带声学索引的联调包，可追加 `--locator <电脑端 locate_wav v2或更新版本>`，并选择新的输出目录，例如 `output/rfid-demo-indexed`。带索引的包需要新版 ARM 定位器。

生成目录包含 `bear-friends-demo-pack.tar.gz`、已编译的 `packs/bear-friends/`、可编辑源素材和 `fixtures/`。上板时在运行目录解压绘本包，得到 `packs/bear-friends`。若板端已有同名绘本包，使用独立的联调目录并让 `--packs` 指向它。绘本包含两个段落、带原文证据的问答和录制好的提示。问题可试「它为什么带伞？」和「它为什么把伞给小兔？」。

`fixtures/cases.json` 标注了跟读、重读和静音的预期结果。`DEMO-INFO.json` 记录参考音和待测音使用的合成声线。默认待测音由同一个中文声线用不同语速重新合成，可用 `--fixture-voice` 指定另一个已安装的声线。合成音只用于联调，真实儿童准确率和板端性能须用独立样本验收。

### 从手机或电脑导入绘本包

应用的设备中心 → 绘本包页面中，先连接阅读器，再点击「从文件导入绘本包」，选择 `.tar.gz` 或 `.tgz` 文件。文件选择器只授权读取所选文件。阅读器需要以 `--packs` 指向一个已存在、可写的绘本目录；导入后的绘本自动进入标签配对清单。同一编号的不同版本返回冲突，当前版本不提供覆盖升级。

电脑可以通过相同接口验证。先创建空的测试绘本目录，运行模拟器，再在另一个终端上传现成的演示包：

```powershell
New-Item -ItemType Directory -Force .\output\import-packs
python .\tools\rfid_simulator.py --state .\output\import-reader.json --packs .\output\import-packs
# 在另一个终端执行：
Invoke-RestMethod 'http://127.0.0.1:8765/v1/storypacks' -Method Post -ContentType 'application/gzip' `
  -InFile .\output\rfid-demo-indexed\bear-friends-demo-pack.tar.gz
Invoke-RestMethod 'http://127.0.0.1:8765/v1/books'
```

自制的已编译绘本可通过 `python tools/storypack_import.py export <packs目录> <bookId> <新的输出.tar.gz>` 打包。板端也可离线执行 `python3 tools/storypack_import.py install <包.tar.gz> <packs目录>`，此命令应在阅读器服务停止时使用。

包中只能包含一本 `packs/<bookId>/` 绘本。导入器逐项解包，限制展开总量为 96 MiB、目录项不超过 2048 个，校验清单中的 SHA-256、元数据、完整 PCM16 音频、FAQ 原文证据与问题模板关联。校验完成前不会发布新书。重复导入完全相同且本地完整的包返回 `alreadyInstalled: true`；同编号不同清单返回 `PACK_ALREADY_EXISTS`。损坏包返回 `PACK_INVALID`，空间或写入失败返回 `IO_ERROR`，可修复后重试。

回归检查：

```powershell
python -B -m unittest discover -s tools -p test_storypack_import.py -v
```

基础用例自行生成原创测试文字和声音，验证导入后绑定、问答、结束会话、重启恢复、游标同步、冲突、损坏包和存储故障。检测到 `output/rfid-demo-indexed` 与 Windows 定位器时，还会通过 HTTP 上传中文演示包，实际运行定位和语音问题匹配，核对跨段、未知问题和静音提示。没有相应资源时该语音用例会明确跳过；合成语音通过不代表真实儿童或板端验收通过。

### 用录音联调自动句段定位

`reader/` 提供 C++17 的 MFCC + DTW 模板定位核心。它接收分块 PCM16，通过能量门限和静音间隔划分一次朗读。先比较当前句段附近的候选，再检查远段能否进入全书前两名。对齐窗包络提供保守的距离下界，只有确定不会改变前两名时才跳过该段的完整 DTW。重复朗读保持原段，跳读可以重定位，全书有近似候选或低置信时保留上次位置。当前版本要求每段参考音频包含约 0.24–8 秒的有效朗读，端点静音阈值暂定为 650 毫秒。[DTW 包络下界参考](https://www.cs.ucr.edu/~eamonn/KAIS_2004_warping.pdf)

以下命令适用于已装有 CMake、Ninja 和 C++17 编译器的 Windows 环境。从仓库根目录运行：

```powershell
$build = "$env:TEMP\tongyue-reader-build"
cmake -S reader -B $build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build $build
& "$build\locate_wav.exe" .\reading.wav s1=.\s1.wav s2=.\s2.wav
```

`reading.wav` 是待定位的 16 kHz 单声道 PCM16 录音，`s1.wav`、`s2.wav` 是各段参考音频。工具输出端点时间、状态、段落、距离和候选差值，也会报告本机处理实时率。参考音频与输入录音应使用独立录制的样本。

启动模拟器时增加 `--locator "$build\locate_wav.exe" --recordings <录音目录>`，并保留 `--packs`。刷卡进入共读后，在 PC 调用 `POST /sim/locate`，例如传入 `{"recording":"reading.wav"}`。参数仅允许指定录音目录内已有的文件名。高置信度结果写入当前会话，结束时摘要标记 `positionSource: audio`。低置信度时，FAQ 返回 `needsPositionConfirmation`，直到再次匹配成功或手动确认段落。模拟器同步的仍是摘要，录音留在电脑上。也可用 `--ctc-model <已固定的电脑模型目录>` 启用可选 CTC 录音流；此模式把词位和重复/不确定状态写入会话轨迹，不改变默认 MFCC 板端运行路径。

PC 模拟器可用 `--allow-free-questions` 打开实验性自由问句。文本问句先匹配审核 FAQ，未命中时只在当前页查找词面重合的原句，按“绘本这一页写着”摘录给出；重合不足或候选歧义时拒答。与 `--ctc-model` 一起使用时，可将本地 16 kHz 单声道 WAV 转写后走同一检索路径。自由提问原文/转写不写入会话摘要，音频在电脑输入目录中由使用者管理。当前模拟器以 JSON 返回文本摘录，没有任意回答的语音合成；板端仍只使用 StoryPack 已审核 FAQ 和预录音回答。这不是语义理解或生成式自由问答。

生成包含自由问句合成样本的电脑演示包后，可以这样启动可选原型：

```powershell
py -X utf8 -B tools/make_demo_storypack.py output/rfid-free-question-demo `
  --locator output/reader-pc/locate_wav.exe
py -X utf8 -B tools/rfid_simulator.py --state "$env:TEMP\tongyue-rfid-free-question.json" `
  --packs output/rfid-free-question-demo/packs --locator output/reader-pc/locate_wav.exe `
  --ctc-model output/ctc-zh-pinned --recordings output/rfid-free-question-demo/fixtures `
  --allow-free-questions
```

刷入演示标签并打开 `bear-friends` 后，`POST /sim/ask` 传入新的中文表达，或 `POST /sim/ask-recording` 传入 `{"recording":"question-free.wav"}`。未知颜色问题 `question-unknown.wav` 应拒答。这个演示验证的是电脑合成语音和词面证据链路，不是儿童实测结果。

目前在 Windows 用中文合成语音验证了同一文本变速、重复、跳段、陌生句、静音、重复模板歧义和过长输入。这个基线在句段端点更新位置，未测真实儿童或跨说话人准确率。端点延迟、板端实时率、内存和录放音仍需在 Hi3516 上验收。

## Linux 板端联调入口

`tools/board_reader.py` 是 Linux 用户态的上板候选程序。它需要支持 POSIX UART 的系统镜像、Python 3.11 或以上、ALSA `arecord` / `aplay`、板端架构编译的 `locate_wav`，以及已编译的 StoryPack 目录。实际板卡镜像是否提供这些接口需要确认。若镜像只提供厂商音频接口或采用 OpenHarmony 小型系统，应接入相应的音频和系统适配层。

### 构建和预检

在安装了 `arm-linux-gnueabihf-g++` 的 Linux 电脑或 WSL 中执行：

```sh
sh tools/package_reader.sh output/tongyue-reader-armv7.tar.gz
```

包内的 `reader-build/locate_wav` 是静态链接的 ARMv7 Linux hard-float ELF。可以用 `CXX=/path/to/arm-linux-gnueabihf-g++` 指定交叉编译器。包包含板端 Python 程序、StoryPack 编译器和验收工具。绘本的授权录音和已编译 StoryPack 需要另行放入板端 `packs/`，服务证书和配对密钥也在部署时单独配置。

将包解压到板端，在解压目录运行预检。替换为实际 UART 和按键设备节点，重复使用 `--book-id` 可检查多本书。

```sh
python3 tools/board_preflight.py --locator ./reader-build/locate_wav \
  --uart /dev/ttyS1 --keys-device /dev/input/event0 --packs ./packs --book-id bear-friends
```

预检会实际启动定位器读取版本，检查 Python 模块、ALSA 命令、节点访问权限和故事包校验。它不打开外设，因此不能证明接线和录放音有效。未配置实体按键时可省略 `--keys-device`。

### 外设与共读验证

1. 把 PN532 模块切到 HSU 模式。按模块和板卡说明确认电平、供电、共地及 TX/RX 对接，选一个未被调试控制台占用的串口。将下方 `/dev/ttyS1` 替换为实际设备节点。驱动按 NXP UM0701-02 实现普通帧、ACK、校验和 Type A 标签读取，UART 配置为 115200、8N1。[NXP PN532 用户手册](https://www.nxp.com/docs/en/user-guide/141520.pdf)
2. 先用 `python3 tools/board_reader.py --uart /dev/ttyS1 --probe` 验证 PN532 固件识别和完整 UID。标签一直贴着时只应出现一条事件，移开再贴后再次出现。
3. 在板端分别确认 ALSA 采集设备和输出设备，再启动阅读器。录音要求 16 kHz 单声道 PCM16，故事包里的 WAV 使用同一格式。

正式记录 G0 结果时，先退出占用 UART 和声卡的阅读器进程，断开公网，再用刚读到的两个实际 UID 运行检查。按终端提示刷卡、朗读和听回放。确认听清测试短句后输入 `yes`。报告只保留 UID、帧数、电平和人工确认结果，临时录音在检查后删除。

```sh
python3 tools/board_acceptance.py peripherals --uart /dev/ttyS1 \
  --uid-a 0004A12B --uid-b 0004A12C > g0-report.json
```

UID 需替换为实际标签值。默认录音 3 秒，要求电平达到 −45 dBFS 且削波样本少于 1%。如使用非默认 ALSA 设备，传入 `--capture-device` 和 `--playback-device`。`g0-report.json` 中的每项 `ok` 都为 `true` 后，再启动完整服务。

```sh
python3 tools/board_reader.py --uart /dev/ttyS1 --state ./reader-state.json \
  --packs ./packs --locator ./reader-build/locate_wav
```

如果默认音频设备不可用，用 `--capture-device` 和 `--playback-device` 传入板端 ALSA 设备名。板端控制台支持 `select s2` 手动选段并播放、`record 5` 录音并定位、`ask 问题文字` 查找当前段的预置证据答案、`ask-voice` 录音提问、`finish` 保存摘要。刷卡打开已绑定的书时播放首段。定长录音的临时文件在处理后删除，系统提供 `/dev/shm` 时优先使用内存文件系统。

### 持续定位和实体按键

启动时加 `--continuous`，会话开始且设备播音结束后，`arecord` 将 PCM 直接送到定位器。持续采音不落盘。设备开始播音、手动选段、暂停监听或结束会话时，停止并清空当前流。控制台用 `listen` 切换自动监听。[ALSA 工具说明](https://raw.githubusercontent.com/alsa-project/alsa-utils/master/aplay/aplay.1)

将板端 GPIO 按键或外接按键映射到 Linux `evdev` 输入节点，确定实际的 `EV_KEY` 键码后传给程序。以下键码仅用于举例。可加 `--no-console` 在无终端的环境运行。[Linux 输入事件协议](https://docs.kernel.org/input/event-codes.html)

```sh
python3 tools/board_reader.py --uart /dev/ttyS1 --state ./reader-state.json \
  --packs ./packs --locator ./reader-build/locate_wav --continuous \
  --keys-device /dev/input/event0 --key-previous 105 --key-next 106 \
  --key-listen 57 --key-finish 28
```

Windows 用实际 C++ 定位器验证了两段中文合成语音在输入流关闭前产生定位事件。WSL 使用按键事件 FIFO 和音频替身验证了播放门控、按键过滤、手动纠正与摘要同步。实际采样延迟、串音、按键设备节点、ARM 资源占用仍需上板验证。

### 为绘本录入离线语音问题

在绘本源 JSON 中增加以下可选字段，再重新编译到新的 StoryPack 输出目录。示例里的 `question` 必须已在该书的 `faq[].questions` 中审核，所需 WAV 与源 JSON 放在同一目录。每本书最多录入 64 个不同问法，每个问法对应一条参考录音。提示音也使用 16 kHz 单声道 PCM16 WAV。

```json
{
  "voiceQuestions": [
    { "question": "它为什么带伞？", "audio": "q1.wav" },
    { "question": "它为什么把伞给小兔？", "audio": "q2.wav" }
  ],
  "voicePrompts": {
    "questionReady": "ready.wav",
    "unsupported": "unsupported.wav",
    "repeatQuestion": "repeat.wav",
    "needsPositionConfirmation": "confirm.wav"
  }
}
```

板端配置 `--key-ask <EV_KEY键码>`。按下后先暂停跟读，可播放 `questionReady` 提示，然后开始采集。检测到约 650 毫秒的句末静音后处理第一句，最长等待录音窗口默认 4 秒。控制台可用 `ask-voice 5` 将窗口上限设为 5 秒。到上限仍没有完整句末时提示重问。问题音频通过管道处理，不写入临时 WAV。

定位器在全书问题录音模板中匹配，问答引擎再按当前确认段落核对已审核证据。匹配分数不足、其它段落的问题和不确定的阅读位置都有单独处理。提示音可按绘本配置，回答或提示播完后恢复跟读。板端日志提供从开始采集到判定的 `captureToDecisionMs`，以及到启动播放器的 `captureToPlaybackDispatchMs`。后者不包含音频设备启动后到真正出声的时间，上板仍需用声学回采验证首个可听音频的延迟。

PC 模拟器可调用 `POST /sim/ask-recording`，传入 `{"recording":"question.wav"}`，文件须在 `--recordings` 目录内。已用独立生成的中文合成语音验证已录入问题、跨段拒答、未知问法、静音、多问和位置不确定提示。WSL 另用替身验证了提问键到播音和恢复跟读的顺序。此实现是有限问法模板匹配，真实儿童跨说话人准确率和 Hi3516 问答延迟仍待实测。

默认服务监听板端的 `127.0.0.1:8765`。开发镜像开启 SSH 时，可从 PC 执行 `ssh -N -L 8765:127.0.0.1:8765 user@板端IP`，再按上文使用 `hdc rport` 连接手机。

已用 WSL 伪串口验证 PN532 帧与 ACK、坏校验恢复、持续贴卡不会满足新扫描、UID 绑定，以及刷卡到摘要同步的调用链。音频命令在该检查中由替身脚本接收。ARM 二进制已通过 Cortex-A7 仿真，实际板端内核、UART 电气、麦克风、扬声器和性能仍待上板验证。

### 手机直连局域网阅读器

1. 为阅读器准备 TLS 服务端证书和私钥。证书的 SAN 填手机要访问的固定 IP 或主机名。由自建 CA 签发时，把 CA 的公开 PEM 证书准备好供手机导入。仅将服务端证书和对应私钥放到板端，CA 私钥留在签发端。
2. 在板端生成每台设备单独的随机配对密钥，并限制密钥文件权限：

   ```sh
   umask 077
   python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > reader-pairing-token.txt
   ```

3. 启动时把 `192.168.1.10` 替换为板端实际 IPv4。对非回环地址开放服务时，必须同时传入证书、私钥和密钥文件。

   ```sh
   python3 tools/board_reader.py --uart /dev/ttyS1 --state ./reader-state.json \
     --packs ./packs --locator ./reader-build/locate_wav --listen 192.168.1.10 \
     --tls-cert ./reader.crt --tls-key ./reader.key --pairing-token-file ./reader-pairing-token.txt
   ```

4. 手机和板端加入同一局域网。在「设备 → 管理阅读器与绘本标签」输入 `https://192.168.1.10:8765` 和密钥。使用自建 CA 时，把公开的 CA PEM 粘贴到证书栏。应用验证 HTTPS，连接成功后将密钥保存在系统资产服务，阅读记录文件只保存设备地址、公开证书、绘本清单和阅读摘要。
5. 更换密钥后重启板端程序，在手机输入新密钥重新连接。开发阶段按配置共享一台阅读器的密钥，换钥会同时撤销此前的连接凭据。

### 采集板端定位验收数据

准备独立于 StoryPack 参考音频的已标注录音。标注文件里的 WAV 路径相对标注文件目录。每条预期事件写 `[状态, 段落ID]`。纯静音用空数组，低置信度保留原位置时填写该原段。

```json
[
  { "recording": "read-repeat.wav", "expected": [["matched", "s1"], ["repeated", "s1"]],
    "positionChecks": [[4500, "s1"], [9000, "s1"]] },
  { "recording": "silence.wav", "expected": [] }
]
```

在 Hi3516 上执行以下命令并保留报告：

```sh
python3 tools/bench_locator.py --locator ./reader-build/locate_wav --packs ./packs \
  --book-id bear-friends --cases ./fixtures/cases.json --acceptance-gates > locator-report.json
```

报告包括预期与实际事件、参考模板准备耗时、进程耗时、P95 实时率和定位器进程峰值 RSS。实时时间包含进程启动和参考模板准备。对同一个带索引的故事包，默认使用预编译特征，加 `--wav-references` 可强制从原始 WAV 重新计算，比较参考准备时间。可以用 `--runner qemu-arm-static --runner-arg=-cpu --runner-arg=cortex-a7` 在 PC 核对 ARM 功能，仿真报告里的耗时和 RSS 只代表仿真环境。仍需在实物上测持续采音稳定性、真实朗读准确率和从提问结束到播音的延迟。

- **G1 特征提取。** `--acceptance-gates` 让定位器单独计时 VAD 和 MFCC，实时率以这两项耗时除以输入录音时长。需要有非静音特征帧、每条录音的分析窗数与预期一致、最大特征实时率小于 0.5。此处的窗数只检查已录 WAV 在定位器内部的处理，实际采音驱动是否丢块仍需核对持续采音日志。
- **G2 句段定位。** `positionChecks` 按时间列出人工标注的检查点，每个独立朗读段选一个检查点，并为端点判定留出静音时间。工具读取当时最近一次定位事件。未知、低置信和错误位置都不计为正确。默认至少检查 10 个位置，准确率目标为 85%。`--min-position-checks 3` 可用于生成器自带的三点合成音调试，用实际独立录音才能做真机验收。
- **G3 跳读恢复。** 在 `expected` 中分别标注同段重读（`repeated`）、跳到远段或回读（`relocated`）、未收录的朗读或远段重句歧义（`uncertain`）。对低置信事件，预期段落仍为上一次确认的段落。实物上需检查此时提问会提示确认段落，按键确认后可以恢复回答。静默翻页不应推进位置。

### 测量问答的实际出声延迟

G4 的研发目标是已知 FAQ 的首个有效回答音频 P95 不超过 2 秒。用同一台双声道录音设备记录每次试验。左声道记录提问者或测试问题播放源，右声道用靠近阅读器扬声器的麦克风回采实际出声。先降低两声道串音，保存为 16、44.1 或 48 kHz 的立体声 PCM16 WAV。每次提问使用独立录音。

人工试听确认回答是否符合预期，然后标出覆盖问题和回答的两个时间窗口。窗口内留出静音边界，回答窗口从「请提问」提示结束后开始，覆盖问题结束前后的区域，以发现提前播音。窗口使用从同一个 WAV 开头算起的毫秒数。标注文件例如：

```json
{
  "captureKind": "hardware",
  "boardModel": "实际板卡型号",
  "imageVersion": "实际系统镜像版本",
  "cases": [
    {
      "recording": "g4-01.wav",
      "questionWindowMs": [1000, 4000],
      "answerWindowMs": [1800, 7500],
      "answerConfirmed": true
    }
  ]
}
```

在板端或电脑运行 `python3 tools/bench_question_latency.py --cases ./g4-cases.json > g4-report.json`。工具使用 10 毫秒采样窗和至少 30 毫秒的连续活动来定位声段。根据试录底噪，可分别用 `--question-dbfs` 和 `--answer-dbfs` 调整阈值。默认要求至少 20 次独立试验，每次都要检测到正确回答，漏答或错误回答不能通过丢弃该条记录来达标。`captureKind: synthetic` 仅用于验证分析流程。实际验收需保留硬件版本、回采和人工确认结果。

### 留存两小时持续采音报告

板端以 `--continuous` 启动阅读器，将标准输出和错误追加到同一个 `reader.log`。刷已绑定绘本，等首段播放完成并看到 `audio: listening`，然后在另一个终端启动观察。将下面的 `1234` 替换为实际 `board_reader.py` 进程 PID。观察期间保持同一个共读会话，避免主动暂停或播音。

```sh
python3 tools/board_acceptance.py observe --pid 1234 --log ./reader.log \
  --seconds 7200 > continuous-report.json
```

定位器 v3 在连续采音时启用 `--stream-progress`，每处理 16000 个输入样本输出一次累计计数。安静时也会报告，报告不会改变阅读位置。阅读器输出 `audio: pcm_progress` 日志，包含阅读器 PID、定位进程 PID、处理样本数和本机单调时间。超过 10 秒没有进展时，阅读器记录 `capture_failed`，清理采音链路并重试。板端脚本和定位器应使用同一交付包；部署预检要求 `tongyue-locate 3`。v3 仍可读取之前编译的声学索引。

观察工具同时检查进程和样本进展。`pcmProgress` 记录本次观察中推进的样本数、更新次数与最大间隔；没有进展、计数倒退、采音进程更换或超过 10 秒的间隔都不能通过。即使进展后来恢复，本次停滞仍会留在结果中。观察还记录 `arecord` 与 `locate_wav` 同时存在的采样数、进程树采样峰值 RSS、采样 CPU，以及观察期间日志中的采集、播放、RFID 和 ALSA 溢出错误。`ok` 和 `meetsTwoHourWindow` 都为 `true` 才表示该次观察覆盖了目标时长。RSS 是进程 RSS 的和，共享页可能重复计入。短间隔故障需结合日志和人工听感，Linux 内核 OOM 或驱动错误需另外留存可访问的内核日志。采样数据来自 Linux `/proc`。[Linux `/proc` 文档](https://docs.kernel.org/filesystems/proc.html)

电脑可用 Linux 或 WSL 验证上述检查逻辑。构建定位器后设置命令，再运行测试；原生 Linux 可直接指定可执行文件，ARM 仿真可在命令前加 `qemu-arm-static -cpu cortex-a7`：

```sh
export TONGYUE_LOCATOR_COMMAND='./reader-build/locate_wav'
python3 -B -m unittest discover -s tools -p test_reader_progress.py -v
```

测试会验证静音时在输入结束前收到进展、进展报告不影响位置事件，并用保持存活但停止写入的替代采音程序验证恢复。短时软件检查不等同于两小时实物验收。

### 电脑上的虚拟板整机回归

`test_board_runtime.py` 在 Linux 或 WSL 中启动实际 `board_reader.py` 子进程，通过伪终端提供 PN532 UART 响应，通过 FIFO 提供 evdev 按键事件，并用独立程序替代 ALSA 录音和播放。需要 Python 3.11 或更新版本、OpenSSL 命令行、`requirements-authoring.txt` 中的编译依赖和已构建的定位器；ARM 定位器可通过 QEMU 执行。测试只访问本机 HTTPS，不打开真实串口、麦克风或扬声器。

从项目根目录运行整个 Python 回归集，例如使用 ARM 仿真：

```sh
export TONGYUE_LOCATOR_COMMAND='qemu-arm-static -cpu cortex-a7 output/reader-arm-progress/locate_wav'
python3 -B -m unittest discover -s tools -p 'test_*.py' -v
```

使用解压的交付包时，把上述命令中的定位器换成 `reader-build/locate_wav`。在 ARM Linux 本机可直接设为 `TONGYUE_LOCATOR_COMMAND='./reader-build/locate_wav'`。没有定位器命令或 OpenSSL 时，整机用例会明确跳过。

该用例自行生成两段原创绘本、声波参考和带证据的问答包，核对以下完整链路：

- HTTPS 证书校验与配对密钥，缺少或错误密钥返回拒绝。
- 上传 StoryPack，经虚拟 UART 扫描保留前导零的 UID 并绑定，持续贴卡只启动一次会话。
- 首段播音后自动定位，按键手动前后选段；按住产生的重复事件和松开事件不重复执行。
- 定时录音经过实际定位器处理，核对实际临时文件已删除。
- 通过提问键匹配已录入问题，答案播完后摘要含原文证据；答案播放器已经启动但中途取消时不写入问答记录。
- 停止并重启实际阅读器进程后，设备 ID、绑定和摘要保留，游标再次拉取无重复。
- 注入 PN532 校验错误后，扫描返回 `RFID_UNAVAILABLE`，驱动重新初始化后恢复，阅读历史不变。

该回归证明软件协议和进程协作，声波匹配结果不代表儿童语音识别准确率。扬声器实际出声、RFID 电气兼容性、板端实时性能与两小时稳定性仍使用前述实物验收方法。

### 家长端服务逻辑的电脑回归

`tools/test_parent_services.cjs` 读取实际 `RfidReader.ets` 与 `ReadingStats.ets`，用 DevEco Studio 自带的 TypeScript 包转换执行。网络、资产存储与文件接口由电脑适配器提供，文件读写使用临时目录；这项检查不运行 ArkUI，也不代表鸿蒙系统接口已经通过真机验证。

在 Windows 项目根目录执行，按本机安装路径替换下面的 DevEco 目录：

```powershell
& 'D:\DevEco Studio\tools\node\node.exe' tools/test_parent_services.cjs `
  'D:\DevEco Studio\sdk\default\openharmony\ets\build-tools\ets-loader\node_modules\typescript'
```

检查覆盖配对请求、证书与错误提示、文件包读取和上传、分页去重、后续页面失败时保留原历史、重复游标拒绝、问答证据校验、设备隔离的定位轨迹校验、位置复核上传/合并/旧值冲突处理、按设备保管密钥、按设备与日期统计，以及缓存写入和同步失败。缓存先以 UTF-8 字节完整写入临时文件，确认所有字节写完并同步后再替换旧文件；短写会继续补写，零字节写入或同步失败不会发布不完整的新历史。输出 JSON 列出每项结果，任一失败时进程返回非零状态。修改 ArkTS 后仍需执行 Hvigor 构建，并在鸿蒙设备上验收文件选择器、系统密钥保管和 UI 操作。
