# 电脑上的 CTC 词位数字样机

本分支读取本地中文录音，在电脑 CPU 上运行流式 CTC，并将识别片段对齐到 StoryPack 原文，输出词界、原文偏移和时间戳。PC RFID 模拟器可把经过 StoryPack 校验的词位写入会话轨迹与摘要；鸿蒙家长端可查看轨迹并保存本机复核标签。实际板端仍默认运行原有 MFCC/DTW，尚未接入 CTC 板端执行。

## 已实现的范围

- `reading_probe.py --backend mfcc|ctc` 输出统一 JSONL。MFCC 标注 `granularity=segment`，CTC 标注 `granularity=word`。MFCC 的起点未知，使用 `null`，不推算词位。
- CTC 按 100 ms 输入块送入独立流状态，模型每次处理 45 帧、前进 32 帧，输出间隔 40 ms 的后验。已处理特征会释放，最多保留 8 秒的对齐窗口。
- 模型声明 `feature=whisper`，使用对应的特征提取和逐块归一化；不能套用普通 Kaldi fbank。此行为与 [sherpa-onnx 流式实现](https://github.com/k2-fsa/sherpa-onnx/blob/master/sherpa-onnx/csrc/online-recognizer-ctc-impl.h) 对照，并通过实际录音与官方运行时的转写一致性检查。
- 支持常用字的词首标签变体，以及“伞”“兔”等字的 UTF-8 字节回退。字节对齐区间先合并为字符，再按作者词界聚合；偏移单位是 Unicode code point。词首别名合并后采用更严格的字符 CTC 重复规则。
- CTC Viterbi 路径包含重复标签之间必须经过 blank 的约束。词时间是标签发射区间，尚未验证为精确的语音学起止边界。
- 先用贪心转写在前后相邻句段中找唯一的完全匹配；没有精确匹配时，仅对已有位置锚点的片段尝试字符级局部 Levenshtein DP。模糊匹配限于 8–128 个字符、最多 1–2 次编辑、至少 80% 字符精确对应，并要求最优位置唯一且与次优句段有足够距离。初始位置未知时不做模糊猜测。再用后验计算转写片段的 CTC 路径。至少需要 4 个可读字符和一个完整词；重复原文有歧义、陌生句或弱声学证据时冻结位置。插入或替换涉及的源字符不会被确认，跨越未匹配字符的词会被省略。部分片段可以确认已经观察到的完整词，不能据此认为整句已读完。
- 17 个连续 blank 帧结束一次片段；超过 8 秒还未结束则输出不确定。片段内有不完整 UTF-8 字节时等待后续输出。文件最长 120 秒，格式为 16 kHz、单声道、PCM16 WAV。

`meanPosterior` 和 `acousticGap` 是研究门控数据，不是儿童朗读准确率或校准置信度。当前只实现短片段的字符级局部编辑距离 DP；没有实现增量书内音节 DP/Beam，也没有把同音字改写为“读对了”。错读、漏读先冻结或只确认能逐字对应的完整词，后续干净片段可重新定位。

## 在当前电脑复现

以下 PowerShell 命令在仓库根目录执行。模型下载固定原作者版本与 SHA-256，镜像只负责传输，不改变校验：

```powershell
py -X utf8 -B tools/fetch_ctc_model.py output/ctc-zh-pinned --base-url https://hf-mirror.com
```

默认下载源也可使用 Hugging Face。模型是 [原作者发布的中文流式 Zipformer CTC](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-ctc/zipformer-ctc-models.html)，固定仓库 `csukuangfj/sherpa-onnx-streaming-zipformer-ctc-zh-int8-2025-06-30`，版本 `f29132af655b91f258800df19c5b5d9b9281a5cd`。ONNX 文件为 162,290,887 字节，SHA-256 为 `24ffdc19ba9aaed5a6a9beaede1e087745217d82425cf4041bca0c696661801e`。文件不会随 ARM 运行包分发。

在 WSL Ubuntu 中准备可选依赖，再运行已有中文示例包：

```sh
cd /mnt/d/Code/a_HarmonyOS/parent-app-rfid
python3 -m venv ~/.venvs/tongyue-ctc
~/.venvs/tongyue-ctc/bin/python -m pip install -r tools/requirements-ctc-pc.txt -r tools/requirements-authoring.txt
~/.venvs/tongyue-ctc/bin/python -B tools/reading_probe.py --packs output/rfid-demo-text-index/packs --book-id bear-friends --backend ctc --model-dir output/ctc-zh-pinned --output output/ctc-reading.jsonl output/rfid-demo-text-index/fixtures/read-repeat.wav
```

当前验证环境已经安装依赖的解释器是 `/home/geqingyu/.venvs/tongyue-authoring/bin/python`，可以直接替换上例解释器。新电脑的中文示例包可先在 Windows 用 `tools/make_demo_storypack.py` 生成，参见 `STORYPACK_FORMAT.md`。

对比 MFCC 路线：

```powershell
py -X utf8 -B tools/reading_probe.py --packs output/rfid-demo-text-index/packs --book-id bear-friends --backend mfcc --locator output/reader-pc/locate_wav.exe --output output/mfcc-reading.jsonl output/rfid-demo-text-index/fixtures/read-repeat.wav
```

两条路线的 `status` 均为 `matched/repeated/relocated/uncertain`。`final=false` 表示当前片段尚未结束；同一次片段的部分进度不会被算成重读。`words` 只列出已完整观察的词，包含 `wordIndex/textStart/textEnd/text/startSeconds/endSeconds/meanPosterior`。不确定事件保留最后确认的句段和词位。

## 验证与剩余工作

算法测试不下载模型，可在装好 NumPy 后执行：

```sh
python -B -m unittest discover -s tools -p test_ctc_alignment.py -v
```

固定异常集覆盖重读、向前跳读、回退、错读、漏读、静音和陌生句；这是构造后验上的算法检查。另有真实模型读取 Windows 中文 TTS 的正文、重读、陌生问题和静音检查，并用官方运行时对照特征和转写。它们没有覆盖真实儿童错读、口音或环境噪声。

可选实际模型检查需额外安装 `sherpa-onnx==2.0.1` 作为对照运行时，并显式指定模型和示例目录：

```sh
python -m pip install sherpa-onnx==2.0.1
TONGYUE_CTC_MODEL=output/ctc-zh-pinned TONGYUE_CTC_DEMO=output/rfid-demo-text-index python -B -m unittest discover -s tools -p 'test_ctc_*.py' -v
```

`bench_ctc_pc.py` 可生成模型版本、转写、词位日志、加载耗时与推理 RTF 报告；该诊断报告包含所选录音的转写，仅适用于明确选定的本地验证材料。

电脑模拟器可显式启用实验性自由问句：先用本模型贪心转写，再在当前页做保守词面检索并原文摘录；它不生成答案、不支持口语同义推理，答案也没有合成语音。实际板端尚未启用这条路径。

后续仍需开发增量书内 DP/Beam、补齐固定音频异常集、实现家长复核跨设备同步，并用授权儿童样本验证准确率。当前模型的内存、速度及依赖尚未在 Hi3516 上验证。若要达到可面向儿童使用的自由提问，还需具备稳定的口语问句识别、书内语义证据召回、证据约束回答与不确定时拒答、离线语音播报，以及真实环境准确率和延迟验收。
