# 示例：Attention Is All You Need（arXiv 1706.03762）

溯源翻译的实测记录。只保存术语表，不保存译文全文。按下面的步骤可以完整复现。

```bash
pdftr translate 1706.03762 --agents 6      # 下载源码、切成 15 块、打印 6 个 agent 的分组
cp examples/1706.03762/glossary.md 1706.03762_zh/work/glossary.md
# 主 agent 在同一条消息里并行启动 6 个子 agent 翻译（见 SKILL.md）
pdftr check 1706.03762_zh                   # 实测：15 块，0 个问题
pdftr assemble 1706.03762_zh --build --bilingual
```

实测（2026-11，Claude Code）：

| 项目 | 结果 |
|---|---|
| 源码 | 10 个 `.tex`，约 3.7 万字符，切成 15 块 |
| 翻译 | 6 个子 agent 并行，约 2 分 20 秒 |
| 校验 | 结构、引用键、数字全部一致 |
| 产出 | `1706.03762_zh.pdf`（14 页）、`1706.03762_zh_bi.pdf`（中英对照，17 页） |
| 质检 | 原文 155 个小数全部保留（“4.5 million”按中文习惯换算成“450 万”）；残留英文只有参考文献 |
