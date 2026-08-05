# 研究：VoxCPM2 前的文字前處理層（ttsfrd 落地判定 + 台灣多音字 G2P）

日期：2026-08-05
對應票：GitHub issue #15（wayfinder T2，Part of #13）
研究問題：任意台灣繁中輸入餵給 VoxCPM2 之前需要哪一層文字前處理。分 (a) **ttsfrd 落地查證**（取得方式、授權能否商用自架、TN 涵蓋項目、是否兼做多音字、來源地讀法差異、開源替代）、(b) **台灣多音字→拼音資源**（涵蓋度、台灣讀音正確性、授權）、(c) **管線組裝與介接**（`raw 繁中 → TN → G2P → VoxCPM2(normalize=False)` 是否成立、兩層 TN 是否衝突）。

**前提**：使用者已指定 TTS 要含 ttsfrd。本票查的是「怎麼落地、什麼阻礙、缺口由誰補」，不是「要不要用」。若查出硬阻礙，須顯著標示並給替代方案。

可信度分級：每條結論標 `primary`（官方 repo／官方文件／實際散布 artifact／教育部資源／本研究第一手實測）或 `community`。社群來源不得單獨作為建議依據。授權一律引用原文或精確定位。無法查證者明寫「未查證」。

查證方法註記：本文對 ttsfrd 的授權判定**不採二手轉述**——直接以 ModelScope API 取回 model 的 licence 欄位與檔案清單、下載 `ttsfrd-0.4.2-cp310-cp310-linux_x86_64.whl` 逐檔檢視 `dist-info/METADATA`、對 338 MB 的 `resource.zip` 以 HTTP Range 讀取 ZIP central directory 取得全部 169 筆檔案清單、再以 Range 抓取並解壓其中的 `COPYING` 檔逐字閱讀。ttsfrd 的能力盤點以二進位 `.so` 的字串表與 C++ mangled 符號取證。TN 行為與 G2P 資源正確性以**本機實跑**取證（VoxCPM2 所用的 `wetext` 正規化器實測、pypinyin 實測、g2pW 實際發行模型的讀音表逐字檢視）。VoxCPM2 介接以 repo 原始碼與官方 usage guide 佐證。

---

## 1. 一句話結論

**建議管線：`raw 繁中 → 我方 TN（wetext + 台灣規則補丁）→ 我方 G2P（g2pW 台灣注音模型，只鎖差異字，輸出 {pinyin+聲調}）→ VoxCPM2(normalize=False)`；順序不可倒置。**

**ttsfrd 依指定形式無法落地——判定為 blocker。** 兩個獨立成立的理由：(1) 該 artifact **完全沒有授權聲明**（wheel 的 `METADATA` 無 `License` 欄、ModelScope model 的 licence 欄位為空字串、repo 內無 LICENSE 檔），著作權預設保留，無任何商用授予可依循；(2) 其 `resource.zip` 內確實含有一份**明文禁止商用**的第三方語料（Festival 的 OALD 詞典），原文為「This software may not be used for commercial purposes without specific prior written permission from the authors.」〔primary，逐字引用見 §2.2〕

**而且即使授權解決，ttsfrd 對本案仍是錯的工具**：其二進位內只有 `LocaleTextAnalyzerZhCN／ZhHK／ZhSC／ZhSH`，**沒有任何 ZhTW locale**，locale 代碼字串只有 `zh-cn`／`zh_cn`／`en-us`／`en_us`，分詞資源目錄只有大陸／香港／上海／四川五套。它的中文路徑就是 zh-CN。〔primary，見 §2.4〕

**替代方案零額外授權風險**：VoxCPM2 內建的正規化器本身就是 `wetext`（WeTextProcessing 的純 Python runtime，Apache-2.0），已在 VoxCPM2 相依樹內。TN 改由我方以同一套 `wetext` 呼叫 + 台灣規則補丁自理即可，不需引入新元件。〔primary〕

**最重要的正面發現（推翻先前判斷）**：`g2pW` **實際發行的模型 `G2PWModel-v2-onnx` 是台灣注音模型，不是大陸拼音模型**。四個標竿差異詞全部正確——`垃`=ㄌㄜ4、`圾`=ㄙㄜ4（首選）、`企`=ㄑㄧ4、`和`含ㄏㄢ4、`期`=ㄑㄧ2（首選）——且 `style='pinyin'` 的輸出格式（小寫拼音 + 聲調數字 1–5）與 VoxCPM2 的 `{le4}` 語法**完全一致、零轉換**。〔primary，見 §3 與 §4.7〕

---

## 2. ttsfrd 落地判定

### 2.1 取得方式（obtainability）

**唯一散布通道是 ModelScope model repo `iic/CosyVoice-ttsfrd`**（作者 `speech_tts`／通義實驗室；建立於 2024-07-05，最後更新 2025-12-17，下載數 340,795）。〔primary〕 https://www.modelscope.cn/models/iic/CosyVoice-ttsfrd

repo 完整檔案清單（ModelScope API `repo/files`，逐筆）：

| 檔案 | 大小（bytes） |
| --- | --- |
| `.gitattributes` | 1,741 |
| `configuration.json` | 47 |
| `README.md` | 8,570 |
| `resource.tar` | 338,876,368 |
| `resource.zip` | 338,914,555 |
| `ttsfrd-0.4.2-cp310-cp310-linux_x86_64.whl` | 4,044,429 |
| `ttsfrd-0.4.2-cp38-cp38-linux_x86_64.whl` | 4,044,404 |
| `ttsfrd_dependency-0.1-py3-none-any.whl` | 1,144,992 |

**無 LICENSE 檔。**〔primary〕

CosyVoice 官方 README 的安裝指令（逐字）：〔primary〕 https://raw.githubusercontent.com/FunAudioLLM/CosyVoice/main/README.md

```
cd pretrained_models/CosyVoice-ttsfrd/
unzip resource.zip -d .
pip install ttsfrd_dependency-0.1-py3-none-any.whl
pip install ttsfrd-0.4.2-cp310-cp310-linux_x86_64.whl
```

同一份 README 逐字：「Optionally, you can unzip `ttsfrd` resource and install `ttsfrd` package for better text normalization performance.」與「Notice that this step is not necessary. If you do not install `ttsfrd` package, we will use wetext by default.」——**上游自己就把 ttsfrd 定位為 optional，預設走 wetext。**〔primary〕

落地限制（實測 wheel 內容）：〔primary〕

- wheel 只含一個編譯產物：`ttsfrd.cpython-310-x86_64-linux-gnu.so`（11,237,128 bytes）+ `dist-info`。**無原始碼、不可審、不可修補、不可重編。**
- 平台鎖死：`Tag: cp310-cp310-linux_x86_64`（另有 cp38）。**只能跑 Linux x86_64 且 CPython 必須是 3.8 或 3.10**，Windows 開發機無法安裝，且會把整個 TTS 容器的 Python 版本綁死在 3.10。
- `ttsfrd_dependency` wheel 內含 `libmvec.so.1`（166,368 bytes）與 `libre2.so.11`（4,184,744 bytes），即隨附 glibc libmvec 與 Google RE2 的預編譯 .so，且**未附任何 notice 檔**。

**PyPI 陷阱（重要）**：PyPI 上**存在**一個名為 `ttsfrd` 的套件，但它是 version `0.1.0`、summary「A minimal example Python package」、wheel 只有 1,315 bytes、2025-03-10 上傳、無作者無授權——**與 Alibaba 的 ttsfrd 毫無關係**。`pip install ttsfrd` 會靜默裝到這個佔名包。〔primary〕 https://pypi.org/pypi/ttsfrd/json

### 2.2 授權判定：**無授權可依循 → 不可商用自架**

四項獨立證據，全部 `primary`：

**(1) wheel 的 METADATA 沒有授權欄位。** `ttsfrd-0.4.2.dist-info/METADATA` 全文（174 bytes，逐字，無省略）：

```
Metadata-Version: 2.1
Name: ttsfrd
Version: 0.4.2
Summary: tts frd engine for python module
Author: jiaqi.sjq
Author-email: jiaqi.sjq@alibaba-inc.com
Requires-Python: >=3.6
```

無 `License:` 欄、無 `Classifier: License ::`、無 `License-File`。

**(2) 相依 wheel 明寫 UNKNOWN。** `ttsfrd_dependency-0.1.dist-info/METADATA` 逐字含 `License: UNKNOWN`。

**(3) ModelScope 平台的授權欄位為空。** `GET https://www.modelscope.cn/api/v1/models/iic/CosyVoice-ttsfrd` 回傳中 `"License":""`、`"LicenseName":""`、`"LicenseLink":""`。repo 檔案清單無 LICENSE。

**(4) model README 沒有授權段，只有免責聲明。** 該 README 是 CosyVoice README 的複本，全文唯一與權利相關的句子是（逐字）：「## Disclaimer / The content provided above is for academic purposes only and is intended to demonstrate technical capabilities. Some examples are sourced from the internet. If any content infringes on your rights, please contact us to request its removal.」——這是對 demo 素材的免責，不是授權授予；而其字面「for academic purposes only」若真被當成使用條件，反而是更強的商用禁止。

**(5) 更硬的一層：resource 內含明文禁止商用的第三方語料。**

`resource.zip` 的 169 筆檔案中包含一整套 Festival 發行物，其中：

- `resource/festival/dicts/oald/COPYING`（2,449 bytes）逐字：

  > Permission to use, copy, modify, distribute this software and its documentation for **research, educational and individual use only**, is hereby granted without fee, subject to the following conditions: ... **This software may not be used for commercial purposes without specific prior written permission from the authors.**

- `resource/festival/voices/english/rab_diphone/COPYING`（2,444 bytes）逐字結尾：

  > festlex_POSLEX.tar.gz fall under the same copyright as the above but **festlex_OALD.tar.gz is free for non-commercial use only.**

- 而受該條款拘束的素材**確實在包裡**：`resource/festival/dicts/oald/ali.oald.lex`（5,216,501 bytes）、`resource/festival/dicts/oald/oald_lts_rules.scm`（2,409,618 bytes）、`resource/festival/dicts/oald/oald_extensions.scm`（58,063 bytes）。檔名 `ali.` 前綴顯示是 Alibaba 改作版本。

**(6) 「CosyVoice 是 Apache-2.0，所以 ttsfrd 也是」的推論不成立。** CosyVoice repo 的授權確為 Apache-2.0（GitHub API `spdx_id: Apache-2.0`）〔primary〕，但：ttsfrd **刻意不在該 repo 內**，而是另一個無授權的 model repo；且 Apache-2.0 的 repo 授權在法律上無法把第三方 CSTR／OALD 素材重新授權。上述 §2.2(5) 的條款是 University of Edinburgh 的，Alibaba 與 OpenBMB 皆無權放寬。

> **BLOCKER 結論**：ttsfrd 依「下載 ModelScope wheel + resource 自架商用」的形式落地，**在授權上不可行**。這不是「授權寬鬆但有雜訊」，而是「根本沒有授予」＋「包內確有禁止商用的成分」兩件事同時成立。
>
> 附帶合規義務（若日後取得商用授權仍須處理）：包內另含要求在二進位散布時重製 notice 的 BSD-3 素材——`resource/jprsc/COPYING` 逐字含 NAIST（2009）、UniDic Consortium（2011–2017）、Open JTalk／Nagoya Institute of Technology（2008–2016）三份 BSD-3；`resource/festival/dicts/cmu/COPYING` 為 CSTR／CMUDICT 的 BSD 式條款。`.so` 內另可見 Festival／Edinburgh Speech Tools、MeCab、MNN、PCRE、RE2、CRF 的符號與 `Copyright(C) 2004-2008 Nippon Telegraph and Telephone Corporation` 字串。

**替代路徑（若使用者仍要 ttsfrd 這個具體元件）**：唯一乾淨的路是向阿里巴巴／通義實驗室取得書面商用授權，並要求其處理 OALD 條款（或提供剔除 OALD 的 resource）。此路可行性**未查證**（見 §6）。在取得前，工程上應走 §2.5 的開源替代。

### 2.3 TN 涵蓋項目

`.so` 為 stripped 但字串表與 C++ mangled 符號完整，可據以盤點。〔primary，取證方式為間接（符號存在≠行為正確），故行為正確性未實測〕

- **數字類**：`num2words` 命名空間下每個 locale 都有 `to_cardinal`／`to_ordinal`／`to_year`／`to_digits`／`to_verbatim`／`to_cardinal_quantity`／`split_by_decimal_point`／`remove_leading_zeros`／`convert_arabic_numerals_to_digits`。另有 `LocalNum2Digits`、`number2Digits_local`、`zhnum:digital`。
- **類別字串**：`cardinal`、`ordinal`、`fraction`、`percent`、`measure`、`currency`、`telephone`、`email`、`address`、`url`、`digit`／`digits`。
- **標點與符號**：`(){}[]`、`"'`.,:;!?{}[]()-"`、`#;&|^$=`'{}()<>` 等標點集字串；`parse_url`、`(parse_url URL)`。
- **韻律／斷句**：`prosody`、`prosodybreak`、`prosodypron`、`tobi_endtone`、`int_tone_cart_tree`（Festival 的 ToBI 語調模組）。
- **對照本票列舉的項目**：數字 ✔、日期（`to_year` + Festival token.scm）✔、時間 ✔（`puncNumberTimeLetter.dict` 資源檔）、金額 ✔（`currency`）、單位 ✔（`measure`）、英文夾雜 ✔（`LocaleTextAnalyzerEnusCnMix`／`EngbCnMix` 專門處理中英混排）、標點 ✔、符號展開 ✔。

**涵蓋面確實比開源替代寬**：ttsfrd 有 `ordinal`／`telephone`／`email`／`address`／`url`，這四類 WeTextProcessing 的中文 TN **沒有**（見 §2.5）。這是放棄 ttsfrd 後我方必須自補的缺口清單。

### 2.4 它是否也做多音字，以及與 (b) 的重疊

**做，而且做到輸出拼音。**〔primary，符號級證據〕

- 字串 `polyphone` 存在。
- 拼音相關符號：`_ZN2ws12CoreDictImpl9getPinyinESsPSt3mapISsSsSt4lessISsESaISt4pairIKSsSsEEEPv`（`ws::CoreDictImpl::getPinyin`）、`ws::ValueParser::getAllpinyin`、`LocaleTextAnalyzerPinYin`。
- lang type 值：`pinyin`、`pinyinfp`、`pinyinvg`、`pinyinvgml`（對應類別 `PinYin`／`PinYinFP`／`PinYinVG`／`PinYinVGML`）。CosyVoice 的呼叫是 `self.frd.set_lang_type('pinyinvg')`。〔primary〕 https://raw.githubusercontent.com/FunAudioLLM/CosyVoice/main/cosyvoice/cli/frontend.py
- 另有旗標字串 `enable_pinyin_mix`、`enable_erhua`、`use_oov_marker`。
- 聲調：`tone0`–`tone6`（七值，含粵語六聲）。

**與 (b) 的重疊程度：功能上 100% 重疊，但方向相反。** ttsfrd 的多音字消歧是 zh-CN 讀法，正是我方要覆蓋掉的那一層；g2pW 的台灣注音模型是我方要的那一層。兩者不是互補而是互斥——若同時使用，ttsfrd 的 zh-CN 讀音會是要被推翻的先驗。

**值得注意的上游訊號**：CosyVoice 呼叫 ttsfrd 時只取 `json.loads(self.frd.do_voicegen_frd(text))["sentences"]` 裡每筆的 `["text"]` 欄位並 `''.join()`，**完全丟棄 ttsfrd 的音素／拼音輸出**。〔primary，逐字見 `cosyvoice/cli/frontend.py` 的 `text_normalize()`〕即上游自己也只把 ttsfrd 當「TN + 斷句」用。

### 2.5 來源地讀法差異（origin-locale divergence）

**硬證據：ttsfrd 沒有台灣 locale。**〔primary，`.so` 符號表逐筆〕

`ILocaleTextAnalyzer` 的實作類別完整清單（28 筆）：

```
ArSA  Byte  DeDE  EnGB  EnUS  EsES  FrFR  IdID  ItIT  JaJP  KoKR  MsMY
PtPT  RuRU  ThTH  TlPH  ViVN  ZhCN  ZhHK  ZhSC  ZhSH
PinYin  Phone97  HKNeural  ZhHKNeural  EngbCnMix  EnusCnMix
```

- 中文系只有 **ZhCN（大陸）、ZhHK（香港）、ZhSC（四川）、ZhSH（上海）**。**無 ZhTW。**
- locale 代碼字串全表只有 `zh-cn`、`zh_cn`、`en-us`、`en_us`。字串表中 `taiwan`／`_TW`／`-TW`／`traditional` 的命中數為 **0**。
- `resource.zip` 的分詞資源目錄為 `ws/`（zh-CN）、`ws_chhk/`、`ws_hk/`（香港）、`ws_chsh/`（上海）、`ws_zhsc/`（四川）——五套，無台灣。
- 存在 `enable_erhua` 旗標（兒化為大陸特徵）。

**因此哪些項目在台灣會讀錯**：由於 ttsfrd 的 zh 路徑就是 zh-CN，凡「兩岸讀法／用詞不同」的 TN 項目都會照大陸讀。**但我無法逐項實跑 ttsfrd（linux-only 二進位）**，所以以下不是 ttsfrd 的實測結果，而是我對「zh-CN 框架的 TN」在同類任務上**實測到的**失效模式（用 VoxCPM2 內建的 `wetext` 取得，見 §4.11）——ttsfrd 屬同一框架、同一 locale 假設，故列為高度可疑項而非已證項：

| TN 項目 | 台灣正確讀法 | zh-CN 框架實測輸出 | 判定 |
| --- | --- | --- | --- |
| 單位 `3kg` | 三公斤 | `三千克` | 錯（用詞） |
| 單位 `1.8m` | 一點八公尺 | `一点八米` | 錯（用詞 + 簡體字） |
| 溫度 `25℃` | 攝氏二十五度 | `二十五摄氏度` | 錯（**語序相反** + 簡體字） |
| 序數 `第 2 名` | 第二名 | `第 两名` | 錯 |
| 電話 `0912-345-678` | 零九一二、三四五、六七八 | `零九百一十二减三百四十五减六百七十八` | 嚴重錯 |
| 金額 `NT$1,250` | 新臺幣一千二百五十元 | `NT一美元,两百五十` | 嚴重錯（誤判為美元） |
| 年份 `2026 年` | 二零二六年 | `两千零二十六年` | 錯 |
| 「一」變調 | 依語境 yī/yí/yì | 不處理（TN 不碰） | 由 G2P 層負責，見 §4.8 |
| 臺／台 | 皆讀 tái | 不轉換（保留原字） | 無讀音影響（見 §4.10） |
| 民國 `115 年` | 民國一百一十五年 | `民國 一百一十五 年` | 正確 |
| 百分比／分數／比例／範圍 | — | `百分之十五`／`四分之三`／`三比二`／`十到二十` | 正確 |

**結論：我方一定要有自己的一層，而且必須在 TN 之後（後處理）或取代 TN 的部分規則。** 「臺 vs 台」不是 TN 問題（兩字同音），是字形一致性問題，可留給 OpenCC 或不處理。

### 2.6 開源替代

| 元件 | 授權（出處） | 中文 TN 規則清單 | 相對 ttsfrd 的差異 |
| --- | --- | --- | --- |
| **WeTextProcessing**（`wenet-e2e/WeTextProcessing`） | Apache-2.0（GitHub API `spdx_id: Apache-2.0`；每個 rules 檔頭皆有 Apache-2.0 標頭，Copyright 2022 Zhendong Peng）〔primary〕 | `cardinal`、`char`、`date`、`fraction`、`math`、`measure`、`money`、`range`、`sport`、`time`、`whitelist`、`postprocessor` | **缺 `ordinal`／`telephone`／`email`／`url`／`address`**。相依 `pynini>=2.1.6`（需 OpenFst，編譯麻煩） |
| **wetext**（`pengzhendong/wetext`） | Apache-2.0（PyPI `license_expression: Apache-2.0`；repo LICENSE 11,357 bytes）〔primary〕 | 同上（以 WeTextProcessing 的規則預編為 FST 隨套件散布） | **不需 pynini**，相依只有 `contractions` + `kaldifst`。**VoxCPM2 內建正規化器就是它** |

**關鍵事實：開源替代已經在相依樹裡。** `VoxCPM/src/voxcpm/utils/text_normalize.py` 第 5 行為 `from wetext import Normalizer`，`TextNormalizer.__init__` 內為 `self.zh_tn_model = Normalizer(lang="zh", operator="tn", remove_erhua=True)`。〔primary〕 https://raw.githubusercontent.com/OpenBMB/VoxCPM/main/src/voxcpm/utils/text_normalize.py

即：改走開源替代**不需要引入任何新的授權面**，只需要「我方主動呼叫 VoxCPM2 已經帶著的那個正規化器，再加上台灣規則補丁」。

**組態陷阱（若改用 WeTextProcessing 本體而非 wetext）**：`tn/chinese/normalizer.py` 的 `Normalizer.__init__` 預設 `traditional_to_simple=True`、`full_to_half=True`、`remove_interjections=True`。**繁體專案必須顯式關掉 `traditional_to_simple`**，否則會把繁中輸入轉成簡體。而 `wetext` 的 `NormalizerConfig` 該旗標預設為 `False`，且 `build_fsts.py` 的 `build_zh_tn()` 明確以 `traditional_to_simple: False` 建核心 FST——所以 VoxCPM2 用的那條路**不會**做繁→簡。〔primary，三個檔案逐字比對〕

---

## 3. 資源比較表

| 資源 | 涵蓋度 | 台灣讀音正確性 | 授權（出處） | 商用自架 | 來源分級 |
| --- | --- | --- | --- | --- | --- |
| **ttsfrd 0.4.2** | TN 最寬（含 ordinal／telephone／email／url／address）＋多音字→拼音＋斷句＋韻律；28 個 locale analyzer 類別、涵蓋 16 種語言 | **無 ZhTW locale**，中文路徑為 zh-CN；讀法為大陸標準 | **無任何授權聲明**；wheel METADATA 無 License 欄；ModelScope licence 欄位空字串；resource 內含明文禁止商用的 OALD 語料 | **不可**（blocker） | primary |
| **wetext**（VoxCPM2 內建） | 中文 TN：cardinal／date／time／money／measure／fraction／math／range／sport／whitelist；**缺 ordinal／telephone／email／url** | 不處理讀音；**TN 輸出本身有台灣錯誤**（單位、溫度語序、序數、電話、NT$；且洩出簡體字 两／点／摄／等于） | Apache-2.0（PyPI `license_expression`、repo LICENSE） | **可** | primary |
| **WeTextProcessing** | 同上（wetext 的上游規則本體） | 同上；另注意預設 `traditional_to_simple=True` 會轉簡體 | Apache-2.0（GitHub `spdx_id`＋各檔 Apache 標頭） | **可** | primary |
| **g2pW 發行模型 `G2PWModel-v2-onnx`** | 多音字 3,582 字（8,022 讀音）＋單音字 9,476 字，union **13,058 字**；含「一」「不」變調 label；套件另附 `char_bopomofo_dict.json`（1.85 MB）作第三層 fallback | **台灣讀音**。實測：`垃`=ㄌㄜ4、`圾`=ㄙㄜ4（首選）、`企`=ㄑㄧ4、`和`含ㄏㄢ4、`期`=ㄑㄧ2（首選）、`髮`=ㄈㄚ3、`蝸`=ㄍㄨㄚ1、`曝`=ㄆㄨ4、`息`=ㄒㄧ2、`質`=ㄓ2（首選）、`液`=ㄧ4（首選）、`寂`=ㄐㄧ2（首選）、`攜`=ㄒㄧ1（首選）、`誰`=ㄕㄟ2（首選）——全部為台灣值 | code：Apache-2.0（repo `LICENCE` 為 Apache-2.0 全文；GitHub `spdx_id: Apache-2.0`；PyPI `g2pw` license `Apache License 2.0`）。**weights zip 內無 LICENSE 檔**（見 §6） | **可**（weights 授權為推定，見缺口 5） | primary |
| **pypinyin 0.55.0** | 單字＋詞語拼音，覆蓋面廣 | **預設大陸讀音**。本機實測：`垃圾`→`la1 ji1`、`他和我`→`he2`、`企業`→`qi3 ye4`、`星期`→`xing1 qi1`——四個標竿全為大陸值 | MIT（GitHub `spdx_id: MIT`；PyPI license `MIT`） | **可**（但需灌台灣讀音字典才可用） | primary |
| **教育部國語辭典系列** | 台灣官方讀音最權威（逐字審訂音／語音） | ground truth | **創用 CC-姓名標示-禁止改作 3.0 臺灣**；官方逐字「允許使用者重製、散布、傳輸著作（**包括商業性利用**）」「但不得修改該著作」 | **可商用**（不得改作，見缺口 7） | primary |
| **教育部《國語一字多音審訂表》** | 只收「台灣內部有多讀音」的字，**不含台灣單音但與大陸不同者**（如 `垃`、`企`） | 官方標準 | 官方頁面為「國語一字多音審訂表（88 年 3 月公告）」；未見官方機讀格式與明文授權條款 | 未查證（見缺口 8） | primary（頁面存在）／未查證（格式與授權） |
| **OpenCC** | 繁簡與詞彙轉換；**不處理讀音** | 與讀音正交。`s2twp` 可把大陸慣用詞換成台灣詞，但 `垃圾` 兩岸同字，對讀音無幫助 | Apache-2.0（GitHub `spdx_id`） | **可** | primary |
| **bert-base-chinese**（g2pW 的 tokenizer 相依） | g2pW 執行必需（`model_source='bert-base-chinese'`） | — | Apache-2.0（HF API `tags: license:apache-2.0`） | **可** | primary |

---

## 4. 管線順序與介接：逐項發現

### 4.1 官方語法與 normalize 的關係〔primary〕

VoxCPM 官方 usage guide 逐字：「Use **phoneme input** only when you need finer pronunciation control. In that case, disable text normalization」；中文「use pinyin with tone numbers such as `{ni3}{hao3}`」；英文「use CMUDict-style phonemes such as `{HH AH0 L OW1}`」；範例 `model.generate(text="{ni3}{hao3}{shi4}{jie4}", normalize=False)`。 https://voxcpm.readthedocs.io/en/latest/usage_guide.html

### 4.2 `normalize` 的預設值已經是 `False`〔primary〕

`src/voxcpm/core.py` 的 `_generate()` 簽章中 `normalize: bool = False`。也就是說「要記得關掉」其實是「不要手動打開」。adapter 只要不傳 `normalize=True` 即可，但**應顯式寫 `normalize=False`** 以防上游改預設。

### 4.3 順序 `TN → G2P` 不可倒置，理由是聲調數字會被 TN 吃掉〔primary，本研究第一手實測〕

我在本機依 `voxcpm/utils/text_normalize.py` 逐字複製 `TextNormalizer.normalize` 的中文路徑，並以 VoxCPM2 完全相同的建構式 `Normalizer(lang="zh", operator="tn", remove_erhua=True)` 實跑，結果：

```
IN : {ni3}{hao3}{shi4}{jie4}
OUT: {ni三}{hao三}{shi四}{jie四}

IN : 我們把{le4}{se4}分類做得很好
OUT: 我們把{le四}{se四}分類做得很好

IN : 他{han4}我一起去{qi4}{ye4}上班，下星{qi1}{qi2}三見面
OUT: 他{han四}我一起去{qi四}{ye四}上班，下星{qi一}{qi二}三見面

IN : {HH AH0 L OW1}
OUT: {HH AH零升 OW一}
```

**TN 把聲調數字展開成中文數字，`{pinyin+聲調}` 標記全毀。** 英文 CMU 音素同樣被毀（`AH0`→`AH零`，且 `L` 被 `measure` 規則當成公升而變成 `升`）。

**因此：任何 TN 都必須跑在 G2P 注入 `{}` 之前。** 這既適用於 VoxCPM2 內建的 normalize（保持 `False`），也適用於任何我方 TN 步驟。原票列的順序 `raw → TN → G2P → VoxCPM2(normalize=False)` **成立，且是唯一可行順序**。

### 4.4 ttsfrd 是否會吃掉已注入的 `{pinyin}`：結構上高度可疑，但**未實測**〔primary（結構）／未查證（行為）〕

- ttsfrd 的 API 是「輸入整句、輸出改寫後的句子」：`do_voicegen_frd(text)` 回 JSON，CosyVoice 取 `["sentences"][i]["text"]`。它是重寫式而非標記保留式。
- 其 TN 含 `to_cardinal`／`to_digits`（§2.3），與 wetext 同類；§4.3 已證同類 TN 會把 `4` 展開。
- 存在旗標 `enable_pinyin_mix`，字面意義是「允許拼音混排」——**若該旗標正是為「文字中混入拼音」設計，就有可能安全**，但其語義、開關方式、輸出格式**官方零文件、原始碼不可讀**。
- **判定：不能假設安全。** 但這一項對決策不再關鍵，因為 ttsfrd 已因授權被排除（§2.2）。

### 4.5 兩層 TN 衝突時該關哪一邊：關 VoxCPM2 那一邊〔primary〕

只要句中出現任一 `{...}` 標記就必須 `normalize=False`（§4.1、§4.3），而 `normalize=False` 會把數字／日期展開整體關掉。**所以 TN 責任必然上移到我方前處理層**，VoxCPM2 內建 normalize 永久停用。這不是取捨，是 `{}` 語法的必然後果。

### 4.6 VoxCPM2 沒有 `{}` 專用解析器，braces 是靠 tokenizer 直接吃進去〔primary〕

`src/voxcpm/model/voxcpm2.py` 對目標文字的處理只有 `text_token = torch.LongTensor(self.text_tokenizer(text))`，其中 `self.text_tokenizer = mask_multichar_chinese_tokens(tokenizer)`、`tokenizer` 為 `LlamaTokenizerFast`。`mask_multichar_chinese_tokens`（`model/utils.py`）只做一件事：把 vocab 中「長度 ≥2 且全為 `一`–`鿿`」的 token 拆成單字。ASCII 的 `{`、拼音字母、聲調數字都不受該 wrapper 影響，直接走 BPE。

推論（標為推論）：`{le4}` 的行為是**訓練時學到的慣例**，不是程式規則。因此 (a) 沒有任何程式路徑會刪掉 braces——安全；(b) 也沒有任何程式會驗證格式——寫錯不會報錯，只會念錯。前處理層必須自己保證格式正確。

### 4.7 g2pW 的輸出格式與 VoxCPM2 語法完全一致，零轉換〔primary〕

`g2pw/api.py` 的 `_convert_bopomofo_to_pinyin`：

```python
def _convert_bopomofo_to_pinyin(self, bopomofo):
    tone = bopomofo[-1]
    assert tone in '12345'
    component = self.bopomofo_convert_dict.get(bopomofo[:-1])
    if component:
        return component + tone
```

即輸出為「小寫拼音 + 聲調數字 1–5」，`5` 為輕聲。實測對照表（`bopomofo_to_pinyin_wo_tune_dict.json`，424 筆）：`ㄌㄜ→le`、`ㄙㄜ→se`、`ㄏㄢ→han`、`ㄑㄧ→qi`、`ㄧㄝ→ye`。

**故 `G2PWConverter(style='pinyin')` 的輸出直接包上 `{}` 就是 VoxCPM2 要的格式**，`{le4}{se4}`、`{han4}`、`{qi4}{ye4}` 與 spike #11 用的字面完全相同。

### 4.8 「一」「不」的變調由 G2P 層決定，不必交給模型猜〔primary〕

發行模型的 `POLYPHONIC_CHARS.txt` 中 `一` 有三個 label：`ㄧ1`／`ㄧ2`／`ㄧ4`；`不` 有 `ㄅㄨ2`／`ㄅㄨ4`／`ㄈㄡ3`／`ㄅㄨ1`／`ㄈㄡ1`／`ㄈㄨ1`。即 g2pW 把「一／不」變調當成上下文消歧任務內建處理。

對照 §4.3 的實測：TN 完全不碰「一」（`一個人一起試一下，第一名是一萬元` 輸出不變），確認變調不是 TN 的職責。

### 4.9 g2pW 三層 fallback：技術上支援全句 G2P，但仍建議只鎖差異字〔primary（機制）／推論（策略）〕

`_prepare_data` 的判定順序（逐字讀 `api.py`）：字在 `polyphonic_chars` → 送模型；否則在 `monophonic_chars_dict` → 查表；否則在 `char_bopomofo_dict` → 取第一個讀音；否則 `None`（非漢字，如英文、數字）。三層覆蓋 13,058 字 + 套件內 1.85 MB 字典。

**策略建議仍為「只鎖差異字」（surgical locking）**，理由：

| 面向 | 全句轉 `{pinyin}` | 只鎖差異字（建議） |
| --- | --- | --- |
| 回歸風險 | 高：本來就念對的字被重寫，錯一個就多一個新錯 | 低：只動確定要改的字 |
| 韻律 | 逐音節 brace 可能壓平 VoxCPM2 原生韻律（未實測） | 保留原生韻律 |
| 與 spike #11 一致 | 不一致 | **一致**（#11 PASS 時只鎖了 4 個詞） |
| 除錯 | 錯音難定位 | 鎖定集合即嫌疑集合 |

差異字集的**自動化產法**（本研究可直接交付的方法）：以 g2pW 的 `MONOPHONIC_CHARS.txt` ∪ `POLYPHONIC_CHARS.txt`（13,058 字、台灣值）逐字轉拼音，diff 掉 pypinyin 的大陸預設，差異者即「必鎖字集」。兩份資料都在手上、授權都可商用。**尚未產出實表**（見缺口 10）。

### 4.10 臺／台 不是 TN 或 G2P 問題〔primary，實測〕

pypinyin 實測 `臺北`→`tai2 bei3`、`台北`→`tai2 bei3`；g2pW 表中 `臺` MONO=`ㄊㄞ2`、`台` POLY=`ㄊㄞ2`（首選）。wetext TN 實測 `臺灣、台灣、臺北、台北、臺灣大學` 輸出完全不變。**兩字同音，前處理層不需要統一字形**（若要統一是文案一致性需求，非發音需求）。

### 4.11 wetext 在繁中上的實測缺陷清單（我方 TN 補丁的工作項）〔primary，本研究第一手實測〕

以 VoxCPM2 相同建構式實跑（完整輸出見 §2.5 表格），另補：

```
IN  : 那儿有一点儿好玩儿      OUT : 那有一点好玩          ← remove_erhua 生效
IN  : 那兒有一點兒好玩兒      OUT : 那兒有一點兒好玩兒      ← remove_erhua 對繁體「兒」不生效
IN  : 台幣 1,250 元，新臺幣 3 萬元   OUT : 台幣 一千二百五十元，新臺幣 三 萬元
IN  : 民國 115 年 8 月 5 日   OUT : 民國 一百一十五 年 八月 五日   ← 正確
IN  : 請撥 02-2345-6789 转 108  OUT : 請撥 零二二三四五六七八九 转 一百零八   ← 分機讀成基數
IN  : 進度 3/4，比例 1:1.5      OUT : 進度 四分之三，比例 一比一点五
IN  : 他說：「1+1=2」，對吧？    OUT : 他說：「一加一等于二」，對吧？
IN  : １２３ 與 123            OUT : 一百二十三 與 一百二十三
IN  : 我用 iPhone 15 Pro 拍的，規格是 A17 Pro 晶片
OUT : 我用iPhone十五Pro拍的，規格是A十七Pro晶片
```

（註：第 3 條測試輸入中的「转」與第 1 條的「儿」是我刻意送入的簡體字元——前者為輸入端筆誤但不影響該條發現，後者用於證明 `remove_erhua` 只對簡體「儿」生效。）

補丁工作項（依嚴重度）：

1. **金額**：`NT$`／`NT`／`元` 的台幣處理（現況把 `NT$` 誤判為美元且被逗號截斷）。
2. **電話與分機**：需自建 telephone 規則（逐位讀 + 分段停頓），wetext 無此規則且現有行為不穩定（`0912-345-678` 讀成基數且把 `-` 讀成「减」；`02-2345-6789` 卻逐位讀對）。
3. **序數**：`第 2` → `第二`（現況 `第 两`）；wetext 無 `ordinal` 規則。
4. **單位台灣化**：`千克→公斤`、`米→公尺`、`公里` 已對。
5. **溫度語序**：`二十五摄氏度` → `攝氏二十五度`。
6. **簡體字外洩**：TN 輸出含 `两`、`点`、`摄`、`等于`——需在 TN 之後過一次 OpenCC `s2tw`（專案已有 `adapters/zh.py` 的 `to_traditional()`）。注意：**此步必須在 G2P 注入 `{}` 之前**，否則 OpenCC 會處理到拼音字母（未實測其對 ASCII 的行為，保守作法是排在 G2P 前）。
7. **年份**：`2026 年` → `二零二六年`（現況 `两千零二十六年`）。
8. **`remove_erhua` 對繁體無效**：若輸入含繁體「兒」需自行處理，或不處理（台灣本來就少兒化，反而是好事）。

### 4.12 輸入淨化：使用者文字中的 `{...}` 必須先剝除〔primary（專案既有慣例）／推論（威脅）〕

專案已有先例：`bff/src/vibe_vox/hotword_text.py` 的模組 docstring 逐字「剝除可能與底層 ASR／LLM 特殊 token 衝突的保留標記（`<|...|>`，含語者與時間戳標記）與控制字元，使 term 注入 context 時以字面 tokenize」。

同一理由適用於 TTS：`{}` 在 VoxCPM2 是音素逃脫語法（§4.6，無驗證、無跳脫機制），使用者可送入 `{shit4}` 之類內容改變發音。**前處理層第一步應剝除或跳脫輸入中原有的 `{`／`}`**，再注入我方標記。

---

## 5. 對下游的影響

### 5.1 前處理層應放在 `TtsClient` **之上**，不放進 adapter

現況（`bff/src/vibe_vox/adapters/base.py` 逐字）：`TtsClient` Protocol 目前只有 `health()`，`synthesize()` **尚未加入**；檔頭 docstring 明示這是「ADR-0001 的唯一 stub 邊界」。

判定與理由：

- `TtsClient` 是 stub 邊界，語義是「模型服務的傳輸 shim」。TN + G2P 是**純函式、確定性、無 I/O**，放進 adapter 會讓它無法在不接模型的情況下單測，也會讓 `StubTtsClient` 與真實 adapter 必須各自複製一份前處理邏輯。
- 專案已有同型模組的慣例：`hotword_text.py`（文字清洗）、`adapters/zh.py`（OpenCC 轉換）皆為獨立純函式模組。建議新增 **`bff/src/vibe_vox/tts_text.py`**（或目錄 `tts_text/`，內含 `tn.py`／`g2p.py`），與上述兩者同層。
- `TtsClient.synthesize()` 定形時，**契約應為「收已前處理完成的文字」**，而 adapter 內固定送 `normalize=False`。這樣「TN 是誰的責任」在型別上就是明確的，不會有兩層都做或兩層都不做的狀態。
- g2pW 需載 ONNX 模型（zip 589,075,404 bytes，解壓後 `g2pw.onnx` 635,212,732 bytes）＋ `bert-base-chinese` tokenizer。載入成本高，應沿用 `adapters/zh.py` 的 `@lru_cache(maxsize=1)` 延遲單例模式。CPU onnxruntime 即可，**不佔 GPU VRAM**，但要計入 image 大小與冷啟動時間。

### 5.2 對 #13 fog「TN + G2P 前處理層設計決策」的收斂

該 fog 現在可以收斂成以下具體決策（本票證據支撐，可直接寫進 ADR／spec）：

| 決策點 | 結論 | 依據 |
| --- | --- | --- |
| TN 元件 | **不用 ttsfrd**（授權 blocker）。用 `wetext`（已在 VoxCPM2 相依樹）+ 我方台灣規則補丁（§4.11 的 8 項） | §2.2、§2.6 |
| G2P 元件 | `g2pw` 預設發行模型（台灣注音）＋ `style='pinyin'`，輸出直接包 `{}` | §3、§4.7 |
| 多音字表選型 | 不需另建字典即可起步：g2pW 模型自帶 13,058 字台灣讀音表 + 套件內 `char_bopomofo_dict.json`。教育部辭典（CC BY-ND，可商用）作為爭議字仲裁與驗收真值 | §3 |
| 策略 | 只鎖差異字（surgical locking），不全句轉拼音 | §4.9 |
| 管線順序 | `淨化 {} → TN → OpenCC s2tw → G2P 注入 {} → VoxCPM2(normalize=False)`，順序不可倒置 | §4.3、§4.11 第 6 項 |
| VoxCPM2 normalize | 永久 `False`（顯式傳，不依賴預設） | §4.1、§4.2、§4.5 |
| 層位置 | 新模組 `bff/src/vibe_vox/tts_text.py`，在 `TtsClient` 之上；`synthesize()` 收已前處理文字 | §5.1 |
| 一／不變調 | 由 g2pW 上下文消歧產出，不交給模型猜 | §4.8 |

### 5.3 對使用者可見行為的一項提醒（需業務判斷，不由我方決定）

若使用者堅持保留 ttsfrd 這個具體元件，唯一合法路徑是取得阿里巴巴的書面商用授權並要求處理 OALD 條款。這是**採購／法務動作，不是工程動作**，且時程不可控。工程上建議先以開源路徑落地（能力上只缺 §4.11 的 8 項規則，且那 8 項本來就要為台灣自寫，ttsfrd 也不會替我們寫對）。

---

## 6. 未查證的缺口

1. **ttsfrd 對已注入 `{pinyin}` 的實際行為** — 未驗證。`.so` 為 linux_x86_64／cp310 專用二進位，本機（Windows）無法載入。**關閉方式**：起一個 `python:3.10-slim` linux/amd64 容器，安裝兩個 wheel 與 338 MB resource，跑 `frd.set_lang_type('pinyinvg'); frd.do_voicegen_frd('我們把{le4}{se4}分類')` 看 `sentences[i]["text"]` 是否保留 `{le4}`。因授權已排除 ttsfrd，此項優先度低。
2. **`enable_pinyin_mix` 與 `set_lang_type('pinyinvg'|'pinyinvgml'|'pinyinfp')` 的語義** — 未驗證，官方零文件、原始碼不可讀。**關閉方式**：同上容器實驗，或向通義實驗室索取文件。
3. **ttsfrd 的 TN 規則與多音字表無法逐條審** — `resource/languagedata_embedded.bin`（323,260,303 bytes）為不透明格式，`ws/xiaoyan.dict`（5,935,276 bytes）亦然。**關閉方式**：只能靠黑箱實跑覆蓋率測試，無法靜態審核。此即「引入不可審二進位」的固有成本。
4. **是否存在可付費取得的 ttsfrd 商用授權** — 未查證。ModelScope 平台服務協議是否對「未聲明授權的 model」隱含授予任何權利，亦未查證。**關閉方式**：法務閱讀 ModelScope 服務協議 + 向阿里巴巴（`jiaqi.sjq@alibaba-inc.com` 為 wheel METADATA 上的作者信箱）詢問商用授權管道。
5. **g2pW 模型權重的授權未在 artifact 內明示** — `G2PWModel-v2-onnx.zip` 只含 `config.py`／`g2pw.onnx`／`MONOPHONIC_CHARS.txt`／`POLYPHONIC_CHARS.txt`／`version`，**無 LICENSE 檔**，且託管於 `storage.googleapis.com/esun-ai/`（玉山銀行 AI 團隊）。repo 為 Apache-2.0 且 README 自述為論文官方 repo，合理推定涵蓋權重，但**這是推定不是明示**。**關閉方式**：向作者（Yi-Chang Chen／GitYCC）確認權重授權，或在 repo 開 issue 請其於 zip 內附 LICENSE。風險等級：中（比 ttsfrd 的「完全無授權」低一階，因 repo 本體明示 Apache-2.0）。
6. **g2pW 台灣讀音只抽驗 18 字，且未實跑模型** — 我逐字檢視了發行模型的讀音表（靜態、確定），但**未下載 589 MB onnx 實跑**，故「上下文消歧的實際準確率」（例如 `和` 在具體句子中會不會選對 ㄏㄢ4、`一` 變調會不會選對）**未驗證**。**關閉方式**：下載模型，以 spike #11 的測試句 + 一組台灣多音字測試集實跑，比對教育部辭典。
7. **教育部辭典 CC BY-ND 的「禁止改作」界線** — 未經法務確認。「以讀音事實建內部查表」是否構成被禁止的 derivative work，涉及「讀音為事實而非著作表達」的判斷。**關閉方式**：法務意見。緩解：本管線的主資源是 g2pW（Apache-2.0），教育部辭典僅作驗收真值與爭議仲裁，不進產品，可大幅降低此風險。
8. **教育部《國語一字多音審訂表》的官方機讀格式與授權** — 官方頁面確實存在且標題為「國語一字多音審訂表（88 年 3 月公告）」，但我**未能確認官方提供 CSV／JSON 等機讀格式**，也未在該頁找到明文授權條款；較新的修訂版審訂成果似以 PDF 經學校端流通。**關閉方式**：直接向教育部終身教育司／國語推行相關單位索取機讀檔與授權說明。註：本管線不依賴此表（g2pW 模型已內含台灣讀音），故此缺口不阻塞。
9. **VoxCPM2 對 `{pinyin+聲調}` 的輕聲（`5`）與變調實際行為** — 沿自 #11 未閉合。官方文件只說「pinyin with tone numbers」，未說明是否照數字面值直讀、`5` 是否被理解為輕聲。**關閉方式**：實測 spike，同一句分別送 `{de5}` 與 `{de2}` 聽測差異。
10. **台陸差異字完整清單尚未產出** — 方法已確定且兩份輸入資料都在手上（g2pW 13,058 字台灣值 diff pypinyin 大陸預設），但**實表未產、未人工複驗**。**關閉方式**：一支 30 行腳本 + 人工抽驗邊界字。這是「只鎖差異字」策略的必要輸入。
11. **選擇性混入 `{pinyin}` 是否劣化整句韻律** — 未驗證。**關閉方式**：A/B 聽測（全漢字 vs 只鎖 4 詞 vs 全句拼音）。
12. **OpenCC `s2tw` 對含 ASCII 拼音字串的行為** — 未實測。§4.11 第 6 項因此保守地把 OpenCC 排在 G2P 之前。**關閉方式**：一行測試 `to_traditional("我們把{le4}分類")`。
13. **wetext 是否有 zh-TW 規則的上游貢獻管道** — 未評估。若我方的台灣規則補丁成熟，回貢上游可省長期維護成本，但 fork／貢獻成本未估。

---

## 附錄：來源清單

### 官方 primary

ttsfrd 與 CosyVoice：
- ModelScope model `iic/CosyVoice-ttsfrd`：https://www.modelscope.cn/models/iic/CosyVoice-ttsfrd
- ModelScope API（licence 欄位為空字串）：`https://www.modelscope.cn/api/v1/models/iic/CosyVoice-ttsfrd`
- ModelScope API 檔案清單：`https://www.modelscope.cn/api/v1/models/iic/CosyVoice-ttsfrd/repo/files?Revision=master`
- `ttsfrd-0.4.2-cp310-cp310-linux_x86_64.whl`（本研究下載並逐檔檢視 `dist-info/METADATA`、`WHEEL`、`RECORD`，以及 `.so` 字串表／符號表）
- `ttsfrd_dependency-0.1-py3-none-any.whl`（`License: UNKNOWN`）
- `resource.zip`（本研究以 HTTP Range 讀 ZIP central directory 取得 169 筆清單，並 Range 抓取解壓 `resource/festival/dicts/oald/COPYING`、`resource/festival/voices/english/rab_diphone/COPYING`、`resource/festival/dicts/cmu/COPYING`、`resource/festival/voices/english/kal_diphone/COPYING`、`resource/jprsc/COPYING`、`resource/festival/languages.scm`、`resource/festival/speech.properties`）
- CosyVoice README（ttsfrd 安裝指令與「we will use wetext by default」）：https://raw.githubusercontent.com/FunAudioLLM/CosyVoice/main/README.md
- CosyVoice `cosyvoice/cli/frontend.py`（`set_lang_type('pinyinvg')`、`do_voicegen_frd`、`text_normalize`）：https://raw.githubusercontent.com/FunAudioLLM/CosyVoice/main/cosyvoice/cli/frontend.py
- CosyVoice repo 授權（GitHub API `spdx_id: Apache-2.0`）：https://api.github.com/repos/FunAudioLLM/CosyVoice
- PyPI 上的同名佔位包（非 Alibaba 版）：https://pypi.org/pypi/ttsfrd/json

TN 開源替代：
- `wenet-e2e/WeTextProcessing`（Apache-2.0；`tn/chinese/rules/` 規則清單；`Normalizer` 預設 `traditional_to_simple=True`）：https://github.com/wenet-e2e/WeTextProcessing
- `pengzhendong/wetext`（Apache-2.0；`NormalizerConfig` 預設 `traditional_to_simple=False`；`build_fsts.py`）：https://github.com/pengzhendong/wetext
- PyPI `wetext`（`license_expression: Apache-2.0`）：https://pypi.org/pypi/wetext/json

VoxCPM2 介接：
- `src/voxcpm/utils/text_normalize.py`（`from wetext import Normalizer`；`TextNormalizer`）：https://raw.githubusercontent.com/OpenBMB/VoxCPM/main/src/voxcpm/utils/text_normalize.py
- `src/voxcpm/core.py`（`normalize: bool = False`；normalize 呼叫點）：https://raw.githubusercontent.com/OpenBMB/VoxCPM/main/src/voxcpm/core.py
- `src/voxcpm/model/voxcpm2.py`（`text_token = torch.LongTensor(self.text_tokenizer(text))`）
- `src/voxcpm/model/utils.py`（`mask_multichar_chinese_tokens`）
- 官方 usage guide（`{ni3}{hao3}`、`{HH AH0 L OW1}`、「disable text normalization」）：https://voxcpm.readthedocs.io/en/latest/usage_guide.html

G2P 資源：
- `GitYCC/g2pW`（Apache-2.0；`g2pw/api.py` 的 `MODEL_URL`、`_convert_bopomofo_to_pinyin`、三層 fallback；`bopomofo_to_pinyin_wo_tune_dict.json`）：https://github.com/GitYCC/g2pW
- 發行模型 `G2PWModel-v2-onnx.zip`（589,075,404 bytes；本研究以 Range 抓取解壓 `POLYPHONIC_CHARS.txt` 8,022 行、`MONOPHONIC_CHARS.txt` 9,476 行、`config.py`）：https://storage.googleapis.com/esun-ai/g2pW/G2PWModel-v2-onnx.zip
- g2pW 論文（Interspeech 2022）：https://arxiv.org/abs/2203.10430
- `mozillazg/python-pinyin`（MIT）：https://github.com/mozillazg/python-pinyin ；PyPI `pypinyin` 0.55.0
- `mozillazg/pinyin-data`（MIT）：https://github.com/mozillazg/pinyin-data
- `BYVoid/OpenCC`（Apache-2.0）：https://github.com/BYVoid/OpenCC
- `google-bert/bert-base-chinese`（Apache-2.0）：https://huggingface.co/google-bert/bert-base-chinese

台灣官方語言資源：
- 教育部國語辭典公眾授權（創用 CC-姓名標示-禁止改作 3.0 臺灣；「包括商業性利用」「但不得修改該著作」）：https://language.moe.gov.tw/001/Upload/Files/site_content/M0001/respub/index.html
- 教育部語文成果網〈國語一字多音審訂表（88 年 3 月公告）〉：https://language.moe.gov.tw/result.aspx?classify_sn=42&subclassify_sn=443&content_sn=28

本研究第一手實測（可重現）：
- wetext 正規化實測：以 `Normalizer(lang="zh", operator="tn", remove_erhua=True)`（VoxCPM2 相同建構式）跑 22 條台灣情境輸入，含 `{pinyin}` 標記、金額、電話、單位、溫度、序數、民國年、全形數字、中英混排。
- pypinyin 0.55.0 預設讀音實測：`垃圾`／`他和我`／`企業`／`星期`／`臺北`／`台北`。
- g2pW 發行模型讀音表逐字檢視：`和`／`期`／`企`／`垃`／`圾`／`為`／`行`／`得`／`重`／`臺`／`台`／`一`／`不`／`髮`／`蝸`／`曝`／`攜`／`危`／`廈`／`誰`／`擴`／`螞`／`夾`／`液`／`寂`／`質`／`息`／`喔`／`嗎`。

### 內部前置事實
- issue #11（CLOSED，PASS）：`normalize=False` + `{le4}{se4}`／`{han4}`／`{qi4}{ye4}`／`{xing1}{qi2}` 鎖定後發音轉台灣音；正式產品需 TTS adapter 加自動前處理層。
- `docs/superpowers/specs/2026-07-24-voxcpm-evaluation.md` §6：VoxCPM2 官方未列台灣國語，靠參考音與音素輸入帶出。
- `bff/src/vibe_vox/adapters/base.py`：`TtsClient` Protocol 目前只有 `health()`，`synthesize()` 待加；ADR-0001 唯一 stub 邊界。
- `bff/src/vibe_vox/adapters/zh.py`：OpenCC `s2tw` 單例（`@lru_cache(maxsize=1)`），既有相依。
- `bff/src/vibe_vox/hotword_text.py`：不可信輸入剝除保留標記的既有慣例。

### community（未作為任何建議的單獨依據）
- 本文未引用任何社群來源作為結論依據。所有授權與能力判定皆來自官方 artifact、官方原始碼、官方授權頁，或本研究第一手實測。
