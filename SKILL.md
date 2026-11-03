---
name: paper-translate-zh
description: 把英文学术论文翻译成中文 PDF，保留原文的图片、表格、公式、配色和版式。输入可以是本地 PDF、arXiv ID 或 arXiv 链接。当用户要求“翻译这篇论文 / 出一版中文 PDF / 中文译本 / 保留格式翻译 / 中英对照”时使用。
---

# 论文中文翻译

产出：**形式不变、只换文字**的中文 PDF。正文、表格、算法、提示词框、附录全部译成中文；图片、公式、表格结构、配色、链接和引用保持原样。可选同时输出中英对照版。

## 第一步：一键入口，判断走哪条路

```bash
pdftr translate <本地 PDF | arXiv ID | arXiv 链接> [--mode fine|quick] [--bilingual]
```

`pdftr` 指本技能目录下的 `pdftr` 脚本（首次使用先运行 `scripts/setup.sh`）。它会自动识别 arXiv ID（包括本地 PDF 侧边的 arXiv 水印），然后：

| 情况 | 走哪条路 | 说明 |
|---|---|---|
| `--mode quick` | **快翻** | 调用 PDFMathTranslate（pdf2zh）在原 PDF 上就地替换文字，一两分钟出稿，适合先看大意 |
| 默认（精翻），arXiv 有 LaTeX 源码 | **溯源翻译**（首选） | 下载 arXiv 源码，直接翻译 `.tex`，公式/表格/参考文献全部来自原文，最后用原模板编译 |
| 默认（精翻），没有源码（非 arXiv 或作者只传了 PDF） | **PDF 重排** | 用本文后半部分的 PDF 重排流程 |

用户没有说明时用精翻；用户说“快速看看 / 粗翻 / 先出个草稿”时用快翻。

## 最重要的原则：图不翻译

- **图片一律保留原图，只翻译图注（caption）。** 包括数据图、曲线图、柱状图、框架图、截图、案例卡片、TikZ 绘图等。不要重绘图片，也不要往图里写中文。
- 溯源翻译中图片直接引用源码里的原图文件；PDF 重排中以原 PDF 的矢量裁剪插入。

## 溯源翻译（arXiv 源码，精翻首选）

`pdftr translate` 发现源码后会自动完成 `fetch` 和 `prep`，并打印分组方案。工程目录结构：

```
<id>_zh/
├── src/            arXiv 原始源码（只读）
├── zh/             预处理后的源码：已注入 ctex 中文支持、XeLaTeX 兼容修正、删除整行注释
├── orig.pdf        arXiv 原 PDF（用于最后质检对比）
└── work/
    ├── manifest.json   切块清单（文件、偏移、字数）
    ├── terms.txt       自动统计的术语候选
    ├── glossary.md     术语表（所有 agent 共用）
    └── chunks/src/NNN.tex → chunks/zh/NNN.tex
```

主 agent 按以下步骤执行：

1. **定术语表**：读 `work/terms.txt` 和摘要、引言对应的块，修订 `work/glossary.md`。所有并行 agent 共用这一份，全文术语才能统一。
2. **并行翻译**：按 `pdftr translate` / `pdftr batches <工程> --agents N` 打印的分组（按字数均衡），**在同一条消息里**用 Agent 工具同时启动 N 个子 agent（默认 6 个；块数少时相应减少）。给每个子 agent 的提示词：

   > 你是论文并行翻译中的一个子 agent。1) 读翻译规则 `<skill>/templates/translate_prompt.md`（忽略其中的占位符）；2) 读术语表 `<工程>/work/glossary.md`；3) 你负责的块：`<ids>`，把 `work/chunks/src/<id>.tex` 译成中文，用 Write 写到 `work/chunks/zh/<id>.tex`（只写译文 LaTeX）；4) 运行 `<skill>/pdftr check <工程>`，只修你自己块的问题，直到通过；5) 用一两句话回报。不要改其他文件。

3. **校验**：`pdftr check <工程>`。它会逐块比对：
   - LaTeX 结构：命令、环境、`\cite`/`\ref`/`\label` 的键、花括号、`$` 的数量；
   - 数字：数字必须与原文一致（million/billion 换算成万/亿的除外）；
   - 漏译：原文英文多、译文中文少的块。
   有问题的块交回对应的子 agent（SendMessage）或自己修。
4. **回填与编译**：`pdftr assemble <工程> --build [--bilingual]`。
   - 输出 `<id>_zh.pdf`；加 `--bilingual` 时另外输出 `<id>_zh_bi.pdf`（每段英文原文在上、中文在下）。
   - 编译后会自动对比 `orig.pdf` 做成品质检（数字缺失、未翻译的英文块）。参考文献、代码、表格中的英文属正常。
5. **看结果**：`pdftr preview --pdf <成品>` 生成缩略拼图，用 Read 检查首页、表格页和图页。

要点：
- 重新运行 `prep` 会按原文内容保留已有译文，可以放心调整切块大小（`--chunk`）后重来。
- 编译失败时先看报错的文件和行号，多半是某块译文改坏了 LaTeX 结构，`check` 通常已经提示。
- 没有 Claude 子 agent 的环境（或者想更快、更省）可以用 API 引擎：`pdftr translate <id> --engine api --workers 8`。它支持 `ANTHROPIC_API_KEY`，以及任意 OpenAI 兼容端点（`PDFTR_API_BASE` / `PDFTR_API_KEY` / `PDFTR_MODEL`），完成后自动 check、assemble、编译。

## 快翻（pdf2zh）

```bash
pdftr translate paper.pdf --mode quick [--service bing|google|deepseek|openai...] [--pages 1-5]
```

- 有 `pdf2zh` 命令就直接用；否则通过 `uvx` 临时运行，已固定好兼容的依赖版本。LLM 类服务需要对应的 API key 环境变量（见 PDFMathTranslate 文档）。
- 输出 `-mono.pdf`（纯中文）和 `-dual.pdf`（双语），并自动质检。
- 已知局限：公式里的文字可能被翻译，页码会变成中文数字，长段落末尾可能被截断，术语不统一。交付时向用户说明，需要高质量时改用精翻。

## PDF 重排（无源码时的精翻）

### 为什么用 LaTeX 重排，而不是在原 PDF 上覆盖中文

在原 PDF 上覆盖文字时，中英文长度不同会导致溢出、行距错乱，公式和引用也会被打散。
LaTeX 重排能得到正常的中文排版，同时可以 1:1 还原表格样式和交叉引用；原文的图则以矢量页面插入。

### 工作流程

所有命令都在工程目录里执行（目录里有 `project.json`）。`pdftr` 指本技能目录下的 `pdftr` 脚本。

#### 0. 环境准备（每台机器一次）

```bash
<skill>/scripts/setup.sh <工程>/project.json   # 建 .venv、装依赖、从系统宋体抽取中文字体
```

还需要 `tectonic`（`brew install tectonic`）。安装 `poppler` 可选。

#### 1. 建工程

`pdftr translate paper.pdf` 在没有源码时会自动建好工程骨架（`project.json` + `tex/`）。也可以手动：

```bash
mkdir -p <工程>/tex && cp <skill>/templates/project.example.json <工程>/project.json
cp <skill>/templates/{preamble.tex,main.tex} <工程>/tex/
```

编辑 `project.json`，至少填好 `source_pdf`。`output_pdf` 不要指向已经交付的文件，避免被覆盖。

#### 2. 通读原文，摸清结构

```bash
pdftotext -layout paper.pdf work/paper.txt     # 全文：用 Read 分段读完，不要漏掉附录
pdftr inspect --pages 1-3 --mode all           # 文字块坐标、字体、色块
pdftr regions                                  # 自动估计每页的图区域和图注位置
pdftr inspect --crop 6 100 80 512 330          # 区域截图（work/crop_p6.png），用 Read 查看样式
pdftr inspect --pages 6 --mode bold --y 120 560   # 表格里哪些单元格是粗体（**x**）或斜体（_x_）
pdftr inspect --pages 20-24 --mode colors      # 色块、提示词框的实际 RGB
```

再用 `pdftoppm -r 50` 渲染全文缩略图，或者对原 PDF 执行 `pdftr preview --pdf paper.pdf`，看一遍整体版式。

#### 3. 定义图区域

把每张图（包括首页的 logo 条、行内小图标）按**在 LaTeX 里出现的顺序**写入 `project.json` 的 `figures`：

- 字段：`name`、`page`、`rect`。不要加 `translate`，默认就是不翻译。
- `rect` 用 `regions` 的输出，左右一般取版心边界（ICLR 为 108–504），上下各留 1–2pt。
- 然后执行：

```bash
pdftr crop     # → tex/figs.pdf：每页一张图，原尺寸、矢量；区域外的文字层已删除
```

- 用 `pdftr preview --pdf tex/figs.pdf` 检查有没有裁掉图例、坐标轴标签，或者混进了正文、图注。
- 图注不能裁进图里，它要在 LaTeX 里译成中文。

#### 4. 写 LaTeX 译文

在 `tex/` 下按章节拆文件（`main.tex`、`refs.tex`、`appendix*.tex`），逐段翻译。示例工程 `examples/2609.39045v1/` 完整展示了下面各种元素的写法。

- **图**：`\begin{figure}[t]\centering\fig{N}\caption{…}\end{figure}`，N 是图在 `figures` 中的序号。窄图且原文是绕排的，用 `wrapfigure`。
- **表**：用 `booktabs` 还原。按 `inspect --mode bold` 的结果还原粗体，底纹用 `\rowcolor`，颜色取自 `inspect --mode colors`。数字原样照抄，并逐一核对。
- **公式**：照抄原文的数学写法；算子名、下标里的英文保持不变（如 `\pi_{\mathrm{controller}}`）。
- **算法**：`algorithm` + `algpseudocode`，注释译成中文。
- **提示词 / 代码框**：`promptbox` + `PV`（Verbatim）。
  - 自然语言部分译成中文，JSON 键、取值枚举、文件名、命令保持原样。
  - 中文行需要手动折行，每行不超过约 36 个汉字。
- **引用**：用 natbib，手写 `\bibitem[Author et~al.(2026)]{key}`，正文用 `\citep{}`。参考文献条目保持英文。
- **系统名的特殊排版**（如 RSIG\textsc{ame}）定义成宏统一使用。
- **原文中坏掉的引用**（如 “Figure F.2.1”）直接改成正确的编号，交付时向用户说明。

#### 5. 编译与检查

```bash
pdftr build                       # tectonic 编译，只打印 error/Overfull，成品复制到 output_pdf
pdftr preview                     # work/sheet_XX.png 缩略拼图，逐张 Read 检查
```

检查清单：

- 没有大片空白页。浮动体不能全部堆到文末；模板已经放宽了浮动参数，大表用 `[tbp]`。
- 没有超出版心的 Overfull。长标识符用 `\small\texttt{}`，再加 `\linebreak` 或 `\hspace{0pt}` 制造断点。
- 首页标题、作者、摘要、logo 条正常显示。
- 表格数字与原文一致，粗体位置正确。
- 页数与原文接近（中文通常多 5–10%）。

## 翻译约定

- **术语首次出现**时可附英文原词，之后统一使用中文。
- **保持英文**：模型名、数据集名、基准名、引擎名、游戏/产品名、代码标识符、参考文献、URL。
- **术语表**：每篇论文开始翻译前先定一张（示例工程中的约定如下），全篇保持一致。
  - controller / explorer / editor / verifier → 控制器 / 探索器 / 编辑器 / 验证器
  - checkpoint → 检查点
  - champion → 冠军（版本）
  - brief → 简报
  - rubric → 评分细则
  - build → 构建（版本）
  - replay → 回放
  - demo → 演示
  - saturation stop → 饱和停止
  - Overall → 总分
  - Base → 基线
- **标题样式**：标题用中文，下面附一行原英文标题。作者名保持英文，机构名译成中文。
- **交付说明**：交付时告诉用户哪些内容保持英文（图内文字、参考文献等），以及做过的修正。

## 踩过的坑

溯源翻译：
- **pdfLaTeX 模板与 XeLaTeX**：`fontenc`/`inputenc`/`pdfsync`、`\pdfoutput` 等会被自动注释掉；模板若用 Times（`times`、`\rmdefault{ptm}`），会自动换成 TeX Gyre Termes（同款字形），否则 XeTeX 会报 “Cannot use XeTeXglyph with ptmr8c”。
- **会议模板里写死的 “Abstract”**：`.sty/.cls` 中 `\bf Abstract` 会被替换为 `\abstractname`（已设为“摘要”）。
- **中英对照**：只对花括号配平、不含 `%`/`\verb` 的普通段落加英文原文；段落数与原文不一致的块只放中文（`assemble` 会提示）。
- **数字核对**：“4.5 million”应译为“450 万”，不要为了通过核对写成“4.5 百万”。

PDF 重排：

- **tectonic 加载 TeX Gyre**：必须按文件名加载（`texgyretermes` + `Extension=.otf`），按字体名会报 “cannot be found”。中文系统字体则可以按名称加载。
- **浮动体堆积**：大表配 `[t]` 加上默认的 `topfraction`，会把所有浮动体推到 `\clearpage` 处。模板已经调大了这些参数。
- **裁图前的清理**：只设置 cropbox 的话，区域外的英文正文仍会作为不可见文字留在成品里，复制文本时会混进来。`pdftr crop` 已先用 redaction 删掉区域外的文字。
- **Verbatim 里的中文**：Verbatim 不会自动折行，必须手动换行。
- 一定不要翻译图片，保留所有图片。
