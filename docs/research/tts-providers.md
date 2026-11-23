# 四家 TTS 服务调研：请求格式与时间戳能力

> 调研时间：2026-11-23。用途：Pic2Video 的配音 + 字幕/时间轴（issue #25）。
> 所有请求样例来自官方文档或仓库既有代码，**未调用任何付费接口**；音色表是抓取时的快照，以各家 voices/list 为准。

## 0. 结论速览

| 服务 | 请求入口 | 时间戳 | 免费额度 | 主要坑 |
| --- | --- | --- | --- | --- |
| Azure Speech（REST） | `POST https://{region}.tts.speech.microsoft.com/cognitiveservices/v1`，body 是 SSML | **REST 不返回**；SDK / WebSocket 有 `WordBoundary`（100ns ticks） | F0：50 万字符/月、20 次请求/60 秒 | 单请求音频最多 10 分钟；SSML 每次最多 50 个 voice/audio 标签、WebSocket 一次 64KB |
| OpenAI `/v1/audio/speech` | `POST https://api.openai.com/v1/audio/speech` | **无**（SSE 只有音频增量 + usage） | 无免费层 | `input` ≤ 4096 字符 |
| 硅基流动（SiliconFlow） | `POST https://api.siliconflow.cn/v1/audio/speech`（OpenAI 形状，字段更宽） | **无** | 有赠送额度，见官网 | `input` ≤ 128000；`voice` 必须写 `模型:音色` |
| MiniMax T2A V2 | `POST https://api.minimax.cn/v1/t2a_v2`（**不是** OpenAI 形状） | **有**：`subtitle_enable` + `subtitle_type=word`，毫秒、词级 | 新用户额度见官网 | 默认返回 hex 音频；>3000 字建议流式 |
| 火山引擎 豆包语音 3.0 | `POST https://voice.cn-beijing.volces.com/api/v3/text-to-speech/{voice_id}/streaming`（**不是** OpenAI 形状） | **有**：`enable_subtitle` → `alignment` 事件，秒、词级 | 按字符计费（汉字按 2 字符），新人资源包见官网 | 响应是 JSONL；WSS 走自定义二进制帧 |
| Gemini TTS | `POST /v1beta/interactions`（新）或 `generateContent`（旧） | **无** | 免费层计费 "Free of charge"（限流在 AI Studio 查） | 3.8 unary 默认返回 **WAV**；多说话人要用 `speech_metadata` |
| Edge TTS | `WSS wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1`（**非官方**） | 有 `WordBoundary`/`SentenceBoundary`（100ns ticks） | 无官方配额，非官方接口 | 要 `Sec-MS-GEC` 令牌，随时可能 403/变更 |

一句话选型：**要同步 HTTP 一把拿音频 + 词级时间戳 → MiniMax 或火山 3.0**；**Azure 音色最好但时间戳只能走 SDK/WebSocket**；**Gemini / 硅基流动只有音频，字幕得自己对齐**；**Edge 免费好用但接口非官方，不可作为产品保障**。

---

## 1. Azure Speech（REST）

文档：<https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-text-to-speech>

### 1.1 端点与请求头

区域端点格式固定：

```text
POST https://{region}.tts.speech.microsoft.com/cognitiveservices/v1
```

常用 region：`eastus`、`eastus2`、`westeurope`、`northeurope`、`eastasia`、`japaneast`、`southeastasia`、`centralindia` 等（同一资源只在创建时选定的 region 可用，写错区域返回 401）。

合成请求头：

| 头 | 值 | 说明 |
| --- | --- | --- |
| `Ocp-Apim-Subscription-Key` | 资源键 | 和 `Authorization` 二选一；**直接用资源键最省事** |
| `Authorization` | `Bearer <token>` | token 有有效期，过期 401 |
| `Content-Type` | `application/ssml+xml` | 写错直接 415 |
| `X-Microsoft-OutputFormat` | 如 `riff-24khz-16bit-mono-pcm`、`audio-24khz-48kbitrate-mono-mp3` | 见 1.4 |
| `User-Agent` | 任意 < 255 字符 | 必填，缺了报 400 |

### 1.2 SSML 必填结构

`<speak>` 根元素三个属性**全部必填**：`version`（当前固定 `"1.0"`）、`xmlns`（`http://www.w3.org/2001/10/synthesis`）、`xml:lang`。`<speak>` 里至少要有一个 `<voice>`。转义：`&`→`&amp;`、`<`→`&lt;`、`>`→`&gt;`；属性值必须加引号（`<prosody volume=90>` 不合法）。

可直接照抄的最小请求：

```http
POST https://eastasia.tts.speech.microsoft.com/cognitiveservices/v1
Ocp-Apim-Subscription-Key: <YOUR_KEY>
Content-Type: application/ssml+xml
X-Microsoft-OutputFormat: riff-24khz-16bit-mono-pcm
User-Agent: pic2video/1.0

<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="zh-CN">
  <voice name="zh-CN-XiaoxiaoNeural">
    <mstts:express-as style="cheerful">今天天气不错，我们出去走走吧。</mstts:express-as>
  </voice>
</speak>
```

常用可控元素：

- `<mstts:express-as style="...">`：情绪/风格（每个音色支持列表不同，见 1.3）。
- `<prosody rate="+10%" pitch="+2Hz" volume="+0%">`
- `<break strength="strong"/>`（x-weak/weak/medium/strong/x-strong = 250/500/750/1000/1250ms）或 `<break time="500ms"/>`（0–20000ms，超限按 20000ms；给了 time 就忽略 strength）。
- `<mstts:silence type="Sentenceboundary" value="300ms"/>`。
- `<phoneme alphabet="ipa" ph="...">`：定制发音（**按字符计费**）。

> 计费提醒（官方原话）："You're billed for each character that's converted to speech, including punctuation ... the service counts optional elements that you use to adjust how the text is converted to speech, like phonemes and pitch, as billable characters."

### 1.3 zh-CN 主要神经音色（快照，实际以 voices/list 为准）

音色枚举接口（返回 JSON 数组，含 `ShortName`/`LocalName`/`Gender`/`StyleList`/`SampleRateHertz`/`WordsPerMinute`/`RolePlayList` 等）：

```bash
# 文档给出的形式：资源自定义域名（路径固定 /cognitiveservices/voices/list）
curl -H "Ocp-Apim-Subscription-Key: $AZURE_KEY" \
  "https://<your-resource-name>.cognitiveservices.azure.com/tts/cognitiveservices/voices/list"
```

主要 zh-CN 音色：

| ShortName | 性别 | 定位 | 常用风格（StyleList 摘录） |
| --- | --- | --- | --- |
| `zh-CN-XiaoxiaoNeural` | 女 | 通用第一选择 | affectionate, angry, assistant, calm, chat, cheerful, customerservice, fearful, gentle, lyrical, newscast, poetry-reading, sad, serious |
| `zh-CN-XiaochenNeural` | 女 | 闲聊/自然对话 | affectionate, angry, calm, cheerful, disgruntled, embarrassed, fearful, gentle, sad, serious |
| `zh-CN-XiaoyiNeural` | 女 | 年轻、活泼 | affectionate, angry, cheerful, disgruntled, embarrassed, fearful, gentle, sad, serious |
| `zh-CN-XiaomoNeural` | 女 | 角色扮演 | 多风格 + 角色（RolePlayList） |
| `zh-CN-XiaoxuanNeural` | 女 | 角色扮演 | angry, calm, cheerful, depressed, disgruntled, fearful, gentle, serious |
| `zh-CN-XiaoruiNeural` | 女 | 老年音 | angry, calm, fearful, sad |
| `zh-CN-XiaoshuangNeural` | 女 | 童声 | chat |
| `zh-CN-YunxiNeural` | 男 | 通用男声 | 多风格 |
| `zh-CN-YunyangNeural` | 男 | 新闻播报 | — |
| `zh-CN-YunyeNeural` | 男 | 故事讲述/角色 | — |
| `zh-CN-YunjianNeural` | 男 | 体育解说 | narration-relaxed, sports-commentary, sports-commentary-excited |
| `zh-CN-YunxiaNeural` | 男 | 少年音 | — |
| `zh-CN-liaoning-XiaobeiNeural` | 女 | 东北话 | — |
| `zh-CN-sichuan-YunxiSichuanNeural` | 男 | 四川话 | — |
| `zh-CN-XiaoxiaoMultilingualNeural` | 女 | 多语种（可读中英混排） | — |

返回字段示例（结构说明，取自文档里的 zh-CN 音色条目）：

```json
{
  "ShortName": "zh-CN-YunxiNeural",
  "LocalName": "云希",
  "Gender": "Male",
  "Locale": "zh-CN",
  "StyleList": ["narration-relaxed", "embarrassed", "fearful", "cheerful", "disgruntled", "serious", "angry", "sad", "depressed", "chat", "assistant", "newscast"],
  "SampleRateHertz": "48000",
  "VoiceType": "Neural",
  "Status": "GA",
  "RolePlayList": ["Narrator", "YoungAdultMale", "Boy"],
  "WordsPerMinute": "293"
}
```

### 1.4 输出格式（X-Microsoft-OutputFormat）

- 流式：`audio-16khz-32kbitrate-mono-mp3`、`audio-24khz-48kbitrate-mono-mp3`、`audio-24khz-96kbitrate-mono-mp3`、`audio-24khz-160kbitrate-mono-mp3`、`ogg-24khz-16bit-mono-opus`、`webm-24khz-16bit-mono-opus`、`raw-24khz-16bit-mono-pcm`、`audio-24khz-16bit-48kbps-mono-opus` …
- 非流式（文件）：`riff-24khz-16bit-mono-pcm`（最常用）、`riff-22050hz-16bit-mono-pcm`、`riff-44100hz-16bit-mono-pcm`、`riff-48khz-16bit-mono-pcm` …

选 48kHz 会触发高清音色模型；非 24/48kHz 由服务端上下采样得到。

### 1.5 时间戳：REST 拿不到，只能走 SDK / WebSocket

**关键结论**：REST v1 合成文档全文没有 word/sentence boundary 的任何说明（关键词 boundary 零命中）——该端点只回音频字节。

时间戳在事件里，通过 Azure Speech SDK（内部走 WebSocket）订阅：

```python
import azure.cognitiveservices.speech as speechsdk

speech_config = speechsdk.SpeechConfig(subscription=KEY, region=REGION)
speech_config.set_property(
    property_id=speechsdk.PropertyId.SpeechServiceResponse_RequestSentenceBoundary,
    value='true')  # 官方注释：Required for WordBoundary event sentences.

synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)

def on_word(evt):
    # audio_offset 单位是 ticks（100ns），+5000 是半个 tick 的舍入
    ms = (evt.audio_offset + 5000) / 10000
    print(ms, evt.text_offset, evt.word_length, evt.duration, evt.text)

synthesizer.synthesis_word_boundary.connect(on_word)
result = synthesizer.speak_ssml_async(ssml).get()   # audio_config=None 时不出声、只拿事件和字节
print(len(result.audio_data))
```

事件对象字段：`boundary_type`、`audio_offset`（ticks）、`text_offset`（字符位置）、`word_length`、`duration`、`text`。官方定义："This event is raised at the beginning of each new spoken word, punctuation, and sentence. The event reports the current word's time offset, in ticks, from the beginning of the output audio."

WebSocket 层的开关（SDK 内部拼的 `speech.config` 文本帧，供自己实现协议时参考，属二手佐证）：

```json
{"context":{"synthesis":{"audio":{
  "metadataoptions":{"sentenceBoundaryEnabled":"false","wordBoundaryEnabled":"true"},
  "outputFormat":"audio-24khz-48kbitrate-mono-mp3"}}}}
```

### 1.6 免费层与限制

| 项 | F0（免费） | S0（标准） |
| --- | --- | --- |
| 神经音色额度 | **50 万字符/月** | 按字符计费 |
| 每秒事务数 | **20 TPS / 60 秒，不可调** | 默认 30 TPS，可申请到 1000 |
| 单请求音频上限 | **10 分钟** | 10 分钟 |
| SSML 中 voice/audio 标签数 | 最多 50 个 | 同 |
| WebSocket 单次 SSML 大小 | 64 KB | 同 |
| 批处理（Batch synthesis） | 不可用 | 可用 |

> 官方注：TTS 的 429 多数不是配额，而是"该 region 该音色后端容量不足"，换 region 或换热门音色比等配额更有效。

**坑**：长文本必须自己分段合成并累加时间偏移（10 分钟/64KB 双限）；每段都要重算 offset 基准；SSML 标签本身不计费但 phoneme 等按字符计费。

---

## 2. OpenAI 兼容 TTS：`POST /v1/audio/speech`

### 2.1 标准请求体（以 OpenAI 官方 OpenAPI 规范为准）

`POST https://api.openai.com/v1/audio/speech`，`Authorization: Bearer $OPENAI_API_KEY`，`Content-Type: application/json`。请求体 `CreateSpeechRequest`，`additionalProperties: false`，**required = `model`、`input`、`voice`**。

| 字段 | 类型/范围 | 说明 |
| --- | --- | --- |
| `model` | `tts-1` / `tts-1-hd` / `gpt-4o-mini-tts` / `gpt-4o-mini-tts-2025-12-15` | 规范里这四个都标注计划 2027-01-06 停服，新集成建议转 Realtime API |
| `input` | string，**maxLength 4096** | 要合成的文本 |
| `voice` | `alloy`/`ash`/`ballad`/`coral`/`echo`/`fable`/`onyx`/`nova`/`sage`/`shimmer`/`verse`/`marin`/`cedar`，或 `{"id":"voice_1234"}` | 自定义音色用对象形式 |
| `instructions` | string，maxLength 4096 | 语气/风格控制；**对 tts-1 / tts-1-hd 无效** |
| `response_format` | `mp3`(默认)/`opus`/`aac`/`flac`/`wav`/`pcm` | — |
| `speed` | 0.25–4.0，默认 1 | — |
| `stream_format` | `audio`(默认)/`sse` | sse 不支持 tts-1 / tts-1-hd |

可直接照抄：

```bash
curl https://api.openai.com/v1/audio/speech \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini-tts","input":"The quick brown fox jumped over the lazy dog.","voice":"alloy"}' \
  --output speech.mp3
```

响应 200 为二进制（`audio/mpeg`、`audio/wav`、`audio/pcm` 等），或 `text/event-stream`。

### 2.2 时间戳：标准 OpenAI TTS 没有

`stream_format=sse` 时只有两种事件：

```json
{"type":"speech.audio.delta","audio":"<base64 音频块>"}
{"type":"speech.audio.done","usage":{"input_tokens":26,"output_tokens":120,"total_tokens":146}}
```

**没有任何 word/sentence boundary 字段**。要字幕只能：① 自己按字符数估算；② 用 ffmpeg 检测静音切句；③ 用外部强制对齐（如 whisper 的 word timestamps）；④ 换一家直接给时间戳的服务。

### 2.3 兼容层差异对照

| | OpenAI 官方 | 硅基流动 | MiniMax | 火山引擎 豆包 |
| --- | --- | --- | --- | --- |
| 是否提供 `/v1/audio/speech` | 是（原生） | **是**（形状兼容） | **否**（`/v1/t2a_v2`） | **否**（`/api/v3/text-to-speech/...`） |
| 鉴权 | `Authorization: Bearer` | 同 | `Authorization: Bearer` | `X-Api-Key`（3.0） |
| 模型名 | `gpt-4o-mini-tts` 等 | `fishaudio/fish-speech-1.5`、`FunAudioLLM/CosyVoice2-0.5B` | `speech-2.8-hd`/`speech-2.8-turbo`/`speech-2.6-*`/`speech-02-*`/`speech-01-*` | `doubao-seed-tts-3.0`（3.0）/ `seed-tts-2.0`（旧） |
| `voice` 取值 | alloy 等 | `"模型:音色"`，如 `FunAudioLLM/CosyVoice2-0.5B:alex` | `voice_setting.voice_id`，如 `male-qn-qingse` | 走 URL 路径 `/{voice_id}` |
| `input` 上限 | 4096 字符 | **128000 字符** | `text` < 10000 字符 | `text` ≤ 10000 字符 |
| 额外字段 | `instructions`、`speed` | `sample_rate`(8000…48000)、`stream`(默认 true)、`gain`(-10..10)、`references`(音色克隆，与 voice 互斥) | `voice_setting`(speed/vol/pitch/emotion)、`audio_setting`(sample_rate/bitrate/format/channel)、`subtitle_enable`、`subtitle_type`、`output_format`(hex/url) | `voice_settings`、`output_format`、`language_code`、`enable_subtitle`、`watermark` |
| `response_format` | mp3/opus/aac/flac/wav/pcm | 仅 mp3/opus/wav/pcm | `audio_setting.format`：mp3/pcm/flac/wav/pcmu_raw/pcmu_wav/opus | `output_format.format`：mp3/pcm/ogg_opus |
| 返回时间戳 | **无** | **无**（也无 usage） | **有，词级毫秒** | **有，词级秒** |

#### 硅基流动（SiliconFlow）

端点：`POST https://api.siliconflow.cn/v1/audio/speech`（国际站 `api.siliconflow.com`）。OpenAI 形状，但 `input` 上限 128000，多了 `sample_rate` / `stream` / `gain` / `references`（音色克隆，audio 可为 URL 或 `data:audio/...;base64`，与 `voice` 互斥）。响应是纯音频二进制，**没有时间戳、没有 usage**。

```bash
curl https://api.siliconflow.cn/v1/audio/speech \
  -H "Authorization: Bearer $SILICONFLOW_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "FunAudioLLM/CosyVoice2-0.5B",
    "input": "今天天气不错。",
    "voice": "FunAudioLLM/CosyVoice2-0.5B:alex",
    "response_format": "mp3",
    "sample_rate": 32000,
    "stream": false
  }' --output out.mp3
```

#### MiniMax T2A V2（唯一在同步接口给词级毫秒时间戳）

端点：`POST https://api.minimax.cn/v1/t2a_v2`，`Authorization: Bearer $MINIMAX_KEY`，`Content-Type: application/json`。**与 OpenAI 完全不兼容**（无 `/v1/audio/speech`）。

```json
{
  "model": "speech-2.8-hd",
  "text": "今天我们来聊一聊，为什么冬天更容易感冒。",
  "voice_setting": {"voice_id": "male-qn-qingse", "speed": 1.0, "vol": 1.0, "pitch": 0, "emotion": "calm"},
  "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
  "subtitle_enable": true,
  "subtitle_type": "word",
  "stream": false,
  "output_format": "url"
}
```

- `subtitle_type`：`sentence`（默认，句级）/ `word`（词级）/ `word_streaming`（仅 `stream=true`）。
- 非流式：返回 `data.subtitle_file`（字幕下载链接，json 格式，**单位毫秒**，句级时每句 ≤ 50 字）。
- 流式：字幕随音频下发在 `data.subtitle`（当前分段）与 `data.subtitles`（最后 `status=2` 的包汇总全部）。
- 字幕对象字段：`T2ASubtitle{text, text_begin, text_end, time_begin, time_end, timestamped_words}`；`T2ASubtitleWord{word, word_begin, word_end, time_begin, time_end}`——`time_*` 单位毫秒、相对本次音频起点，`*_begin/_end` 是在合成文本中的字符位置。
- 音频：`data.audio`，`output_format` 为 `hex`（默认）时是 hex 字符串、`url` 时是 24 小时有效的链接；流式只能 hex。
- `extra_info`：`audio_length`（毫秒）、`audio_sample_rate`、`audio_size`、`bitrate`、`audio_format`、`audio_channel`、`word_count`、`usage_characters`（计费字符数）。
- 错误：`base_resp.status_code`，0 正常、1002 限流、1039 TPM 限流、1004 鉴权失败、1042 不可见字符比例 >10%。

#### 火山引擎 豆包语音（无 OpenAI 兼容层）

方舟（ark）侧 TTS 全是自有协议（`/api/v3/plan/tts/bidirection`、`.../unidirectional/stream`、`POST .../unidirectional`），**搜不到 `/v1/audio/speech`**。豆包语音 3.0 见下一节。

---

## 3. 火山引擎 豆包语音 3.0：JSONL + alignment 时间戳

文档：<https://docs.volcengine.com/docs/doubao-voice/seed-tts-3-0-intro>（页面是 SPA，**纯文本可取 <https://docs.volcengine.com/docs/zh/doubao-voice/seed-tts-3-0-intro.md>**）。

- 接入域名：`voice.cn-beijing.volces.com`，鉴权头 **`X-Api-Key`**。
- 三个合成入口：单向流式 HTTP `POST /api/v3/text-to-speech/{voice_id}/streaming`、双向流式 `WSS /api/v3/text-to-speech/{voice_id}/streaming-input`；另有 `POST /api/v3/tts/voice_clone`、`/api/v3/tts/get_voice`、`/api/v3/tts/voice_design`。
- 计费：汉字（含日文汉字）每字计 **2 字符**，字母/假名每字符 1，标点/空格/回车各 1（"你好，world!" = 11 计费字符）。

### 3.1 可照抄的 HTTP 请求

```bash
curl -X POST "https://voice.cn-beijing.volces.com/api/v3/text-to-speech/{voice_id}/streaming" \
  -H "X-Api-Key: $VOLC_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "今天我们来聊一聊，为什么冬天更容易感冒。",
    "model_id": "doubao-seed-tts-3.0",
    "language_code": "zh-CN",
    "output_format": {"format": "mp3", "sample_rate": 24000, "bit_rate": 64000},
    "voice_settings": {"speed": 1.0, "volume": 1.0, "pitch": 0},
    "enable_subtitle": true
  }'
```

请求体字段（官方 3.0 文档）：`text`（与 `ssml` 二选一，≤10000 字符）、`ssml`、`model_id`（默认 `doubao-seed-tts-3.0`）、`language_code`（默认 `auto`，可用 `zh-CN-x-sichuan` 之类方言码）、`output_format{format,sample_rate,bit_rate}`、`voice_settings{speed,volume,pitch,instructions,similarity_boost,dialect_boost}`、**`enable_subtitle`**、`session_id`、`watermark`、`text_normalization`、`enable_markdown_filter`、`silence_duration`（0–30000ms，只加在整段末尾）。

### 3.2 响应：JSONL 事件流

`200 OK`，`Content-Type: text/plain; charset=utf-8`，按行拆 JSON，顶层 `event` 区分：

```json
{"event":"ready","session_id":"..."}
{"event":"audio","audio":"<base64 音频片段>"}
{"event":"alignment","text":"今天我们来聊一聊","words":[{"word":"今天","startTime":0.0,"endTime":0.32,"phonemes":[]}]}
{"event":"final","is_final":true,"reason":"TTS_STREAM_FINAL_REASON_CLOSED"}
```

- 音频要按接收顺序把 `audio` 的 base64 解码后拼接。
- **`alignment` 只在 `enable_subtitle=true` 且语种支持时返回**，`startTime`/`endTime` **单位是秒**，`words` 是词/字级——这是火山给字幕最直接的方式。
- `error` 事件：`{type, code, message, param, request_id, logid}`。

### 3.3 双向流式 WSS（增量文本）

`wss://voice.cn-beijing.volces.com/api/v3/text-to-speech/{voice_id}/streaming-input`，头 `X-Api-Key`。客户端事件：`init`（把 model_id/language_code/output_format/voice_settings/**enable_subtitle**/watermark/text_normalization 都放这里）、`text{content}`（增量文本）、`flush`（提交当前批）、`interrupt`（打断）、`close`（收尾）；服务端同样用 `alignment` 回时间戳，结尾 `final`。

### 3.4 旧版 v3 协议（历史文档，仍在用）

域 `openspeech.bytedance.com`，头 `X-Api-App-Id` / `X-Api-Access-Key` / `X-Api-Resource-Id`（如 `seed-tts-2.0`），WebSocket 二进制帧（大端：byte0 左 4bit 版本 `0b0001`、右 4bit header size；byte1 message type + flags；byte2 左序列化（`0b0000` Raw / `0b0001` JSON）、右压缩（`0b0000` 无 / `0b0001` gzip））。时间戳参数在 `req_params.audio_params` 下：

| 参数 | 效果 | 适用范围 |
| --- | --- | --- |
| `enable_timestamp` | 在 `TTSSentenceEnd` 事件里加该子句时间戳；"一个子句的时间戳返回之后才会开始返回下一句音频" | 仅 TTS1.0 / ICL1.0 音色 |
| `enable_subtitle` | 新增 `TTSSubtitle` 事件，字/词粒度（原文，非 tn 归一化后文本） | 仅 TTS2.0 / ICL2.0 音色 |

**坑**：`enable_subtitle` 在不传 `ssml` 时不生效；开了 latex tn 就不返回字幕；SSML 不支持字幕；只支持中英。

---

## 4. Gemini TTS（2026-11 主线已切到 Interactions API）

文档：<https://ai.google.dev/gemini-api/docs/speech-generation>

### 4.1 模型与 2.5 / 3.x 差别

| 模型 | 说明 | 接口 |
| --- | --- | --- |
| `gemini-3.8-flash-tts` | 主线，音质/表现力最好，支持 voice design/replication/remixing | `interactions` |
| `gemini-3.8-flash-lite-tts` | 便宜快，替代 `gemini-3.1-flash-tts-preview` | `interactions` |
| `gemini-3.1-flash-tts-preview` | 过渡版，默认返回**裸 PCM** | `interactions` / `generateContent` |
| `gemini-2.5-pro-preview-tts` | 2.5 代 Pro TTS | `generateContent` |
| `gemini-2.5-flash-preview-tts` | 2.5 代 Flash TTS（输入 8192 / 输出 16384 tokens） | `generateContent` |

主要差别：

1. **接口**：2.5 代走 `POST /v1beta/models/{model}:generateContent` + `generationConfig.speechConfig`；3.8 代官方示例走 `POST /v1beta/interactions` + 每段 `annotations: [{"type":"speech_metadata", ...}]`。
2. **多说话人写法**：2.5 用 `speechConfig.multiSpeakerVoiceConfig.speakerVoiceConfigs`；3.8 用 `generation_config.speech_config: {"speakers":[{"speaker","voice"}]}` + 每个 part 的 `speech_metadata.speaker`。
3. **默认输出格式**：`gemini-3.1-flash-tts-preview` 及更早的预览版 unary 默认**裸 PCM（`audio/l16`）**；**3.8 unary 默认返回完整 WAV（`audio/wav`，带 RIFF 头）**，streaming 默认仍是裸 `audio/l16`。
4. **风格控制**：3.8 把 `text` 当逐字稿，持续语气放 `style`，瞬时发声用尖括号内联标签（`<laugh>`、`<sigh>`、`<cough>`、`<breath>`、`<short pause>`）；2.5 时代习惯把"朗读指导"直接写进文本。

### 4.2 单说话人（可直接照抄，3.8）

```bash
curl -X POST "https://generativelanguage.googleapis.com/v1beta/interactions" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.8-flash-tts",
    "input": [{
      "type": "user_input",
      "content": [{
        "type": "text",
        "text": "今天我们来聊一聊，为什么冬天更容易感冒。",
        "annotations": [{"type": "speech_metadata", "style": "warm and friendly"}]
      }]
    }],
    "response_format": {"type": "audio"},
    "generation_config": {"speech_config": [{"voice": "Kore"}]}
  }'
```

- `voice` 可取 30 个预置音色（`Kore`、`Puck`、`Aoede`、`Fenrir`、`Charon`…）或 `voice_...`（voice design / replication 生成）、`GET /v1beta/voices` 的扩展库。
- REST 返回里音频是 **`steps[].content[].data`（base64）**；SDK 用 `interaction.output_audio`。

### 4.3 多说话人（每 part 的 speech_metadata）

```bash
curl -X POST "https://generativelanguage.googleapis.com/v1beta/interactions" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.8-flash-tts",
    "input": [{
      "type": "user_input",
      "content": [
        {"type": "text", "text": "今天感觉怎么样？",
         "annotations": [{"type": "speech_metadata", "speaker": "Joe", "style": "cheerful and friendly"}]},
        {"type": "text", "text": "还不错，你呢？",
         "annotations": [{"type": "speech_metadata", "speaker": "Jane", "style": "calm and relaxed"}]}
      ]
    }],
    "response_format": {"type": "audio"},
    "generation_config": {
      "speech_config": {"speakers": [
        {"speaker": "Joe", "voice": "Puck"},
        {"speaker": "Jane", "voice": "Kore"}
      ]}
    }
  }'
```

注意：**字段名是 `speech_metadata`（下划线）**；单说话人 `speech_config` 是**数组** `[{"voice":"Kore"}]`，多说话人是**对象** `{"speakers":[...]}`。

仓库里 `E:\trae code\podcastfy\tools\gemini_tts.py` 用的是旧 `generateContent` 路径：`POST {API_ROOT}/{model}:generateContent`，头 `x-goog-api-key`，body `{"contents":[{"parts":[{"text":...}]}],"generationConfig":{"responseModalities":["AUDIO"],"speechConfig":...}}`，从 `candidates[0].content.parts[].inlineData` 取 base64 与 mimeType，采样率从 mimeType 里的 `rate=(\d+)` 读、兜底 24000。它给 3.x 模型时把每句拆成独立 part 并加 `speechMetadata: {speaker, style}`（camelCase），否则报 `400 Multi-speaker generation requests must specify speech_metadata.speaker for each text part in the contents`——这条错误信息本身就是格式说明，但**官方 3.8 文档已改用 interactions 的 `annotations[].type=speech_metadata`**，迁移时按官方为准。

### 4.4 PCM 采样率与输出格式

| `response_format.mime_type` | 说明 |
| --- | --- |
| `audio/wav`（unary 默认） | 完整 WAV，RIFF 头，**24 kHz 单声道 16-bit signed LE PCM** |
| `audio/l16`（streaming 默认） | 无头裸 PCM，**24 kHz 单声道 16-bit signed LE** |
| `audio/mulaw` | G.711 μ-law，8 kHz，电话场景 |
| `audio/alaw` | G.711 A-law，8 kHz |

可选 `sample_rate`：24000（默认）/16000/8000。要自己封 WAV 就取 `audio/l16` 后按 24kHz、16-bit、单声道补头。

### 4.5 免费层与限额

- 价格（2026-11 官网）：3.8 Flash TTS 输入 $0.50/1M 文本 token（2026-12-31 前，2027-01-01 起 $1.00），输出 $9.00/1M 音频 token（起 $18.00），约 **$0.00225/10 秒音频**；3.8 Flash-Lite TTS 输出 $6.00/1M（约 $0.0015/10 秒）；**音频 token = 25 tokens/秒**；Batch 无免费层。
- **免费层**：pricing 页 Free Tier 两行均为 **"Free of charge"**（免费但会计入限流）。
- **RPM/TPM/RPD 官方已不再公布固定表**：rate-limits 页现在写 "can be viewed in Google AI Studio"，只保留 Batch enqueued tokens 表（如 2.5 Flash TTS 免费 100,000）。社区实测报告（论坛，仅供参考）：<https://discuss.ai.google.dev/t/paid-tier-1-prepay-project-stuck-on-free-tier-quota-for-tts-models-429/203685> 里 429 报错为 `Quota exceeded for metric generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 10, model: gemini-2.5-flash-tts … per day limit exceeded on Free Tier`，帖中称控制台 Tier 1 的 TTS 值是 **10 RPM / 100 RPD**。→ **上线前以 AI Studio 控制台实际数值为准**。
- 限制：TTS 模型只吃文本、只出音频，不支持 function calling / 结构化输出 / context caching / Live API。

### 4.6 时间戳

**Gemini TTS 不返回任何时间戳**（官方 speech-generation 页搜 "timestamp" 零命中）。只能靠：音频时长 + 文本长度估算、静音切分、或外部强制对齐。

---

## 5. Edge TTS（非官方接口）

来源：`rany2/edge-tts` 源码（`constants.py` / `communicate.py` / `drm.py`），走的是 Edge 浏览器"大声朗读"的私有 WebSocket。

### 5.1 协议入口

```text
wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1?TrustedClientToken=6A5AA1D4EAFF4E9FB37E23D68491D6F4
```

- 音色列表：`https://speech.platform.bing.com/consumer/speech/synthesize/readaloud/voices/list?trustedclienttoken=<同一 token>`
- 必需的"浏览器伪装"：`Origin: chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold`、`Pragma: no-cache`、`Cache-Control: no-cache`、`Sec-WebSocket-Version: 13`、Edge 143 的 UA；还需要 `Sec-MS-GEC` / `Sec-MS-GEC-Version` 头（`drm.py` 用 `WIN_EPOCH` 时间片 + `TRUSTED_CLIENT_TOKEN` 做 SHA-256，输出大写 hex 生成），`Sec-MS-GEC-Version` 形如 `2-143.0.3650.75`。缺这些会 403。

### 5.2 两条控制帧

1）`speech.config`（开时间戳就靠这里）：

```text
X-Timestamp:<RFC1123 时间>
Content-Type:application/json; charset=utf-8
Path:speech.config

{"context":{"synthesis":{"audio":{"metadataoptions":{"sentenceBoundaryEnabled":"false","wordBoundaryEnabled":"true"},"outputFormat":"audio-24khz-48kbitrate-mono-mp3"}}}}
```

2）`Path:ssml` 文本帧，SSML 结构与 Azure 同源：

```xml
<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'>
  <voice name='zh-CN-XiaoxiaoNeural'>
    <prosody pitch='+0Hz' rate='+0%' volume='+0%'>今天天气不错。</prosody>
  </voice>
</speak>
```

文本要先转义、去掉不兼容字符，并按 **4096 字节**切片分多次请求。

### 5.3 时间戳：audio.metadata + 100ns ticks

服务端下发 `Path:audio.metadata` 文本帧：

```json
{"Metadata":[
  {"Type":"WordBoundary",
   "Data":{"Offset":1250000,"Duration":2500000,"text":{"Text":"今天","Length":2,"BoundaryType":"WordBoundary"}}}
]}
```

- `Type` ∈ `WordBoundary` / `SentenceBoundary`（由 `metadataoptions` 里两个开关决定哪个出现），另有 `SessionEnd` 可忽略。
- **时间单位是 100 纳秒 ticks：1 秒 = 10,000,000 ticks**，`ms = Offset / 10000`。
- 音频在二进制帧 `Path:audio`（`Content-Type: audio/mpeg`）。

**最大的坑：多段拼接的偏移漂移。** 每段 SSML 是独立一次合成，第二段开始的 `Offset` 又从 0 起算，必须自己加累计偏移；而 edge-tts 现在不用"上一段 offset+duration"累加（长文本会因 AI 静音长短和微软整数溢出错位），而是用 CBR 字节数换算：

```text
ticks = cumulative_audio_bytes * 8 * 10_000_000 // 48_000   # 输出是 48kbps CBR mp3
```

### 5.4 配额与风险

**没有官方 API key、没有官方配额文档**。这是 Edge 浏览器"大声朗读"的私有接口，微软可以随时改协议、加校验或封 IP/UA；`Sec-MS-GEC` 就是反滥用的产物（时钟偏差过大也会 403）。可用于本地工具/原型，**不适合作为商业产品的唯一 TTS 通道**。

---

## 6. 给 Pic2Video 的落地建议

1. **要"一条请求拿到音频 + 词级时间戳"**：优先 MiniMax T2A V2（`subtitle_type=word`，毫秒、词级、同步 HTTP），或火山豆包 3.0（`enable_subtitle` → `alignment`，秒级、词级）。
2. **要中文音色质量与稳定 SLA**：Azure，但时间戳必须用 SDK 的 `synthesis_word_boundary`（REST 拿不到），并按 10 分钟/64KB 分段、自己累加 ticks。
3. **纯免费/离线原型**：Edge TTS，WordBoundary 都能拿到；但必须接受"非官方、随时可能挂"。
4. **Gemini / 硅基流动**：只当"生成音频"用，字幕另做对齐（whisper 强制对齐或静音切分）。
5. **OpenAI 官方 TTS**：不建议作为本项目主力——没有时间戳、input 只有 4096 字符、且模型已标注 2027-01 停服。

---

## 7. 参考

- Azure REST TTS：<https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-text-to-speech>
- Azure SSML 结构：<https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-structure>
- Azure 合成（WordBoundary 示例）：<https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-speech-synthesis>
- Azure 配额：<https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-services-quotas-and-limits>
- Azure 价格：<https://azure.microsoft.com/en-us/pricing/details/cognitive-services/speech-services/>
- OpenAI OpenAPI 规范：<https://github.com/openai/openai-openapi>
- 硅基流动 TTS：<https://docs.siliconflow.com/en/api-reference/audio/create-speech>
- MiniMax T2A V2：<https://platform.minimax.cn/docs/api-reference/speech-t2a-http>
- 火山豆包语音 3.0：<https://docs.volcengine.com/docs/doubao-voice/seed-tts-3-0-intro>
- Gemini Speech generation：<https://ai.google.dev/gemini-api/docs/speech-generation>
- Gemini 价格：<https://ai.google.dev/gemini-api/docs/pricing>
- edge-tts 源码：<https://github.com/rany2/edge-tts>
