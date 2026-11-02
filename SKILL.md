---
name: paper-translate-zh
description: 把英文学术论文 PDF 翻译成中文 PDF，保留原文的图片、表格、公式、配色和版式。当用户给出论文 PDF 并要求“翻译成中文 / 出一版中文 PDF / 中文译本 / 保留格式翻译”时使用。
---

# 论文 PDF 中文翻译

产出：一份**完整的中文 PDF**。正文、表格、算法、提示词框、附录全部译成中文；图片、公式、表格结构、配色、链接和引用保持原样。

工具：`pdftr`（本目录下，Python + PyMuPDF）负责分析 PDF 和裁图；正文用 LaTeX（ctex + tectonic）重新排版。
示例：`examples/2609.39045v1/` 是一篇 47 页 ICLR 论文的完整工程（配置 + 全部 .tex），遇到不确定的写法时直接参照它。

## 最重要的原则：图不翻译

- **图片一律保留原图，翻译图注（caption）。** 包括数据图、曲线图、柱状图、框架图、截图、案例卡片等。不要重绘图片，也不要往图里写中文。
- 只有当用户**明确要求**“图里的文字也要翻译”时，才使用可选的图内翻译模式（见文末）。
- 图在成品里以原 PDF 的矢量裁剪形式插入（`pdftr crop`），所以清晰度不会下降。

## 为什么用 LaTeX 重排，而不是在原 PDF 上覆盖中文

在原 PDF 上覆盖文字时，中英文长度不同会导致溢出、行距错乱，公式和引用也会被打散。
LaTeX 重排能得到正常的中文排版，同时可以 1:1 还原表格样式和交叉引用；原文的图则以矢量页面插入。

## 工作流程

所有命令都在工程目录里执行（目录里有 `project.json`）。`pdftr` 指本技能目录下的 `pdftr` 脚本。

### 0. 环境准备（每台机器一次）

```bash
<skill>/scripts/setup.sh <工程>/project.json   # 建 .venv、装依赖、从系统宋体抽取中文字体
```

还需要 `tectonic`（`brew install tectonic`）。安装 `poppler` 可选。

### 1. 建工程

```bash
mkdir -p <工程>/tex && cp <skill>/templates/project.example.json <工程>/project.json
cp <skill>/templates/{preamble.tex,main.tex} <工程>/tex/
```

编辑 `project.json`，至少填好 `source_pdf`。`output_pdf` 不要指向已经交付的文件，避免被覆盖。

### 2. 通读原文，摸清结构

```bash
pdftotext -layout paper.pdf work/paper.txt     # 全文：用 Read 分段读完，不要漏掉附录
pdftr inspect --pages 1-3 --mode all           # 文字块坐标、字体、色块
pdftr regions                                  # 自动估计每页的图区域和图注位置
pdftr inspect --crop 6 100 80 512 330          # 区域截图（work/crop_p6.png），用 Read 查看样式
pdftr inspect --pages 6 --mode bold --y 120 560   # 表格里哪些单元格是粗体（**x**）或斜体（_x_）
pdftr inspect --pages 20-24 --mode colors      # 色块、提示词框的实际 RGB
```

再用 `pdftoppm -r 50` 渲染全文缩略图，或者对原 PDF 执行 `pdftr preview --pdf paper.pdf`，看一遍整体版式。

### 3. 定义图区域

把每张图（包括首页的 logo 条、行内小图标）按**在 LaTeX 里出现的顺序**写入 `project.json` 的 `figures`：

- 字段：`name`、`page`、`rect`。不要加 `translate`，默认就是不翻译。
- `rect` 用 `regions` 的输出，左右一般取版心边界（ICLR 为 108–504），上下各留 1–2pt。
- 然后执行：

```bash
pdftr crop     # → tex/figs.pdf：每页一张图，原尺寸、矢量；区域外的文字层已删除
```

- 用 `pdftr preview --pdf tex/figs.pdf` 检查有没有裁掉图例、坐标轴标签，或者混进了正文、图注。
- 图注不能裁进图里，它要在 LaTeX 里译成中文。

### 4. 写 LaTeX 译文

在 `tex/` 下按章节拆文件（`main.tex`、`refs.tex`、`appendix*.tex`），逐段翻译。示例工程完整展示了下面各种元素的写法。

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

### 5. 编译与检查

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

- **tectonic 加载 TeX Gyre**：必须按文件名加载（`texgyretermes` + `Extension=.otf`），按字体名会报 “cannot be found”。中文系统字体则可以按名称加载。
- **浮动体堆积**：大表配 `[t]` 加上默认的 `topfraction`，会把所有浮动体推到 `\clearpage` 处。模板已经调大了这些参数。
- **裁图前的清理**：只设置 cropbox 的话，区域外的英文正文仍会作为不可见文字留在成品里，复制文本时会混进来。`pdftr crop` 已先用 redaction 删掉区域外的文字。
- **Verbatim 里的中文**：Verbatim 不会自动折行，必须手动换行。
- 一定不要翻译图片，保留所有图片。
