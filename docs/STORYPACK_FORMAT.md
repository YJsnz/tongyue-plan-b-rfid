# StoryPack 文本与音节索引

新编译的包沿用外层 `formatVersion: 1`，新增 `textIndex` 描述和三个列入 SHA-256 清单的文件：`segments.bin`、`phoneme_lexicon.bin`、`policy.json`。旧包可继续用于 B0 句段功能；它们不会被声明为已有音节索引。

## 离线编译

仅在内容编译电脑安装依赖：

```powershell
py -m pip install -r tools/requirements-authoring.txt
py -X utf8 -B tools/storypack.py <作者JSON> <输出packs目录> --locator <locate_wav路径>
```

拼音转换使用固定版本的 [pypinyin 官方接口](https://pypinyin.readthedocs.io/zh-cn/master/api.html)，保留数字声调，轻声用 `5`。索引保存字典读音，不声称已模拟儿童发音、口音或语流变调。板端读取编译结果只需要标准库，不需要 pypinyin 或整个拼音词典。

作者可在段落中提供 `chapterId`、`storyId` 和 `words`：

```json
{
  "segmentId": "s1", "chapterId": "c1", "storyId": "umbrella",
  "text": "小熊带着伞。", "audio": "s1.wav",
  "words": ["小熊", "带着", "伞"]
}
```

`words` 必须按顺序覆盖所有可朗读文字，允许词间标点和空白。没有作者词界时，按汉字及已有覆盖词条建立基本单元，不进行中文词法分词。非汉字、专名和多音词可通过书籍顶层 `lexicon` 提供读音候选，例如：

```json
{"lexicon": {"乐乐": [["le4", "le4"], ["yue4", "yue4"]]}}
```

首个候选为作者指定首选。自动读音采用整句上下文；作者覆盖仅对本包生效，不修改全局字典。词界和偏移一律是原文的 **Unicode 码点**，左闭右开，不是 UTF-8 字节数或 ArkTS 字符串的 UTF-16 长度。

## 二进制布局

数值使用无符号 32 位小端整数。字符串为 `u32 UTF-8字节数 + UTF-8内容`。文件不允许尾随数据。

| 文件 | 布局 |
| --- | --- |
| `phoneme_lexicon.bin` | 8 字节 `TYLEX001`，词条数；每项为词面字符串、候选数；每个候选为音节数及音节字符串。 |
| `segments.bin` | 8 字节 `TYSEG001`，段落数；每段为 segmentId、chapterId、storyId、原文四个字符串，词数，再跟词记录。 |
| 词记录 | `u32 startCodePoint, u32 endCodePoint, u32 lexiconId`，lexiconId 从 0 开始。 |

上限为 1024 段、100000 个首选音节、每词最多 8 个候选、每候选最多 64 个音节、每个索引文件 8 MiB。读取器检查完整性、词条引用、词面、原文覆盖及 JSON/二进制的一致性。导入时即做检查；日常 B0 阅读只按需装载细粒度索引。

`StoryPack.text_index().tokens(segmentId)` 返回音节、词编号、段内 tokenIndex 和原文跨度。汉字数与音节数相同时逐字映射；否则映射到整个作者词跨度。此映射是对齐参考数据，当前 MFCC 运行时仍输出句段位置，CTC/词位时间对齐另行开发。

## 策略与声明

`policy.json` 声明适龄范围、已审核问法、当前段证据范围、低置信确认、未知问题拒答和默认不保留原始音频。当前运行时不支持的策略值会被拒绝。`book.json.authorization` 保存来源声明，标注 `declaration_only`；编译器不把作者声明当作法律授权证明。

验证索引和 100 条查询追溯：

```powershell
py -X utf8 -B -m unittest discover -s tools -p test_text_index.py -v
```

完整虚拟板回归的 Python 环境也需安装上述编译依赖，因为回归会现场生成绘本。日常模拟器、板端读卡和已有包播放不需要该依赖。
