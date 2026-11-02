#!/usr/bin/env python3
"""pdftr —— 学术论文 PDF → 中文 PDF 的辅助工具集。

整体思路：正文用 LaTeX(ctex + tectonic) 重排；矢量图里的文字用 PyMuPDF
就地"擦除英文 + 写入中文"；位图原样保留；所有图按区域裁成独立页，供 LaTeX
\\includegraphics[page=N] 引用。

子命令（均读取 project.json，见 templates/project.example.json）：
  fonts      从系统 TTC 中抽取中文字体（宋体常规/粗体）到 fonts_dir
  inspect    页面结构/字体/图片/色块/粗体单元格/区域截图，帮助确定图区域与表格样式
  regions    自动估计矢量图区域（非正文字体文字 + 绘图 + 图片的聚类）及图注位置
  extract    抽取各矢量图中的文字单元 → units.json / todo.txt（待翻译清单）
  render     按翻译表把中文写回矢量图 → figs_zh.pdf
  crop       把所有图（原图或中文图）裁成独立页 → <latex_dir>/figs.pdf
  build      在 latex_dir 中用 tectonic 编译，并复制成品
  preview    把 PDF 渲染成缩略拼图（contact sheet），便于检查版面
"""
import argparse, collections, importlib.util, json, os, re, shutil, subprocess, sys

try:
    import pymupdf
except ImportError:
    sys.exit('需要 PyMuPDF：pip install pymupdf fonttools pillow')


# ---------------------------------------------------------------- 配置
def load_cfg(path):
    cfg = json.load(open(path))
    base = os.path.dirname(os.path.abspath(path))
    def P(k, d=None):
        v = cfg.get(k, d)
        return v if v is None or os.path.isabs(v) else os.path.join(base, v)
    cfg['source_pdf'] = P('source_pdf')
    cfg['work_dir'] = P('work_dir', 'work')
    cfg['latex_dir'] = P('latex_dir', 'tex')
    cfg['fonts_dir'] = P('fonts_dir', 'fonts')
    cfg['translations'] = P('translations', 'figtrans.py')
    cfg['output_pdf'] = P('output_pdf')
    cfg.setdefault('body_font_prefixes', ['Nimbus', 'CM'])
    cfg.setdefault('figures', [])
    cfg.setdefault('auto_align_rules', [])
    os.makedirs(cfg['work_dir'], exist_ok=True)
    return cfg


def is_body_font(name, cfg):
    return name.startswith(tuple(cfg['body_font_prefixes']))


# ---------------------------------------------------------------- fonts
FONT_SOURCES = {  # macOS 默认位置；其他系统请在 project.json 中用 font_files 指定
    'regular': ('/System/Library/Fonts/Supplemental/Songti.ttc', 'Songti SC Regular'),
    'bold': ('/System/Library/Fonts/Supplemental/Songti.ttc', 'Songti SC Bold'),
}


def cmd_fonts(cfg, a):
    from fontTools.ttLib import TTCollection
    os.makedirs(cfg['fonts_dir'], exist_ok=True)
    for key, (ttc, face) in cfg.get('font_sources', FONT_SOURCES).items():
        out = os.path.join(cfg['fonts_dir'], f'zh-{key}.ttf')
        if os.path.exists(out):
            print('exists', out); continue
        c = TTCollection(ttc)
        for f in c.fonts:
            if f['name'].getDebugName(4) == face:
                f.save(out); print('saved', out); break
        else:
            print('未找到字体', face, '于', ttc, file=sys.stderr)


def font_files(cfg):
    ff = cfg.get('font_files') or {}
    reg = ff.get('regular') or os.path.join(cfg['fonts_dir'], 'zh-regular.ttf')
    bold = ff.get('bold') or os.path.join(cfg['fonts_dir'], 'zh-bold.ttf')
    for f in (reg, bold):
        if not os.path.exists(f):
            sys.exit(f'缺少字体 {f}，先运行 `pdftr fonts`')
    return {False: reg, True: bold}


# ---------------------------------------------------------------- inspect
def cmd_inspect(cfg, a):
    d = pymupdf.open(cfg['source_pdf'])
    pages = parse_pages(a.pages, len(d))
    for pn in pages:
        p = d[pn - 1]
        td = p.get_text('dict')
        print(f'=== page {pn}  ({p.rect.width:.0f}x{p.rect.height:.0f})')
        if a.mode in ('blocks', 'all'):
            for b in td['blocks']:
                x0, y0, x1, y1 = b['bbox']
                if b['type'] == 1:
                    print(f'{x0:5.0f}{y0:5.0f}{x1:5.0f}{y1:5.0f}  [IMAGE]'); continue
                t = ' | '.join(''.join(s['text'] for s in l['spans']) for l in b['lines'])
                print(f'{x0:5.0f}{y0:5.0f}{x1:5.0f}{y1:5.0f}  {t[:100]}')
        if a.mode in ('fonts', 'all'):
            c = collections.Counter()
            for b in td['blocks']:
                if b['type']: continue
                for l in b['lines']:
                    for s in l['spans']: c[s['font']] += len(s['text'])
            print('fonts:', dict(c))
        if a.mode in ('colors', 'all'):
            for dr in p.get_drawings():
                r = dr['rect']; f = dr.get('fill')
                if r.width > 20 and r.height > 3 and f:
                    print('  fill', [round(v) for v in r], tuple(round(x * 255) for x in f))
        if a.mode == 'bold':   # 表格粗体/斜体单元格：**粗** _斜_
            rows = {}
            for b in td['blocks']:
                if b['type']: continue
                for l in b['lines']:
                    for s in l['spans']:
                        if a.y and not (a.y[0] <= s['bbox'][1] <= a.y[1]): continue
                        t = s['text']
                        if re.search(r'Medi|Bold|Black', s['font']): t = f'**{t}**'
                        if re.search(r'Ital|Obli', s['font']): t = f'_{t}_'
                        rows.setdefault(round(s['origin'][1]), []).append((s['bbox'][0], t))
            for y in sorted(rows): print(y, ' '.join(t for _, t in sorted(rows[y])))
    if a.crop:
        pn, *r = a.crop
        out = os.path.join(cfg['work_dir'], f'crop_p{int(pn)}.png')
        d[int(pn) - 1].get_pixmap(matrix=pymupdf.Matrix(a.zoom, a.zoom), clip=pymupdf.Rect(r)).save(out)
        print('saved', out)


def parse_pages(s, n):
    if not s: return list(range(1, n + 1))
    out = []
    for part in s.split(','):
        if '-' in part:
            x, y = part.split('-'); out += list(range(int(x), int(y) + 1))
        else: out.append(int(part))
    return out


# ---------------------------------------------------------------- regions
def cmd_regions(cfg, a):
    """按"非正文字体文字 + 绘图 + 图片"纵向聚类，估计每页的图区域。"""
    d = pymupdf.open(cfg['source_pdf'])
    for pn in parse_pages(a.pages, len(d)):
        p = d[pn - 1]; rects = []; caps = []
        for b in p.get_text('dict')['blocks']:
            if b['type'] == 1:
                rects.append(pymupdf.Rect(b['bbox'])); continue
            t0 = ''.join(s['text'] for s in b['lines'][0]['spans'])
            if re.match(r'(Figure|Table|Fig\.)\s*\d', t0): caps.append((round(b['bbox'][1], 1), t0[:40]))
            for l in b['lines']:
                for s in l['spans']:
                    if not is_body_font(s['font'], cfg): rects.append(pymupdf.Rect(s['bbox']))
        for dr in p.get_drawings(): rects.append(dr['rect'])
        rects = [r for r in rects if r.height < p.rect.height * 0.9]
        rects.sort(key=lambda r: r.y0); cl = []
        for r in rects:
            if cl and r.y0 < cl[-1].y1 + a.gap: cl[-1] |= r
            else: cl.append(pymupdf.Rect(r))
        cl = [c for c in cl if c.height > 5]
        if cl or caps:
            print(f'page {pn}:', [tuple(round(v, 1) for v in c) for c in cl])
            for c in caps: print('   caption', c)


# ---------------------------------------------------------------- extract
def cmd_extract(cfg, a):
    d = pymupdf.open(cfg['source_pdf'])
    units = []
    for fg in cfg['figures']:
        if not fg.get('translate'): continue
        fid, pn = fg['name'], fg['page']; R = pymupdf.Rect(fg['rect'])
        p = d[pn - 1]; n = 0
        for b in p.get_text('dict')['blocks']:
            if b['type']: continue
            cur = None
            for l in b['lines']:
                sp = [s for s in l['spans'] if not is_body_font(s['font'], cfg)]
                if not sp: continue
                lb = l['bbox']
                if not R.contains(pymupdf.Point((lb[0] + lb[2]) / 2, (lb[1] + lb[3]) / 2)): continue
                main = max(sp, key=lambda s: len(s['text']))
                size, x0 = main['size'], lb[0]
                # 只有"常规体"开头的段落才把后续同缩进、同字号、紧邻的行并入（粗体多为独立标签）
                if (cur and abs(cur['x0'] - x0) < 2 and abs(cur['size'] - size) < 0.25
                        and lb[1] - cur['bbox'][3] < 1.2 * size and cur['dir'] == l['dir']
                        and 'Bold' not in cur['font0']):
                    cur['lines'].append(sp); cur['bbox'] = list(pymupdf.Rect(cur['bbox']) | lb)
                    cur['origins'].append(list(sp[0]['origin']))
                else:
                    n += 1
                    cur = dict(id=f'{fid}.{n}', fig=fid, page=pn, x0=x0, size=size, dir=l['dir'],
                               bbox=list(lb), lines=[sp], font0=main['font'], origins=[list(sp[0]['origin'])])
                    units.append(cur)
    out = []
    for u in units:
        allsp = [s for ln in u['lines'] for s in ln]
        c = collections.Counter()
        for s in allsp: c[(s['font'], s['color'])] += len(s['text'])
        dom = c.most_common(1)[0][0]
        parts = []
        for ln in u['lines']:
            seg = ''
            for s in ln:
                t = s['text']
                if (s['font'], s['color']) != dom and t.strip():
                    tag = 'b' if 'Bold' in s['font'] else ('i' if 'Ital' in s['font'] else 'span')
                    seg += f'<{tag} c={s["color"]:06x}>{t}</{tag}>'
                else: seg += t
            parts.append(seg.strip())
        txt = re.sub(r'\s+', ' ', ' '.join(parts))
        u.update(text=txt, font=dom[0], color=f'{dom[1]:06x}', nlines=len(u['lines']),
                 spans=[[s['bbox'], s['text']] for s in allsp])
        del u['lines']; out.append(u)
    jp = os.path.join(cfg['work_dir'], 'units.json')
    json.dump(out, open(jp, 'w'), ensure_ascii=False, indent=0)
    tp = os.path.join(cfg['work_dir'], 'todo.txt')
    k = 0
    with open(tp, 'w') as f:
        f.write('# id  字号  字体  颜色  行数  [bbox]  原文（<b c=..> 为与主样式不同的片段）\n')
        for u in out:
            if not re.search(r'[A-Za-z]{2,}', re.sub(r'<[^>]+>', '', u['text'])): continue
            k += 1
            f.write(f"{u['id']:9s} {u['size']:.1f} {u['font'][-8:]:8s} {u['color']} L{u['nlines']} "
                    f"[{','.join(str(round(v)) for v in u['bbox'])}] {u['text']}\n")
    print(f'{len(out)} 个单元，其中 {k} 个含英文待译 → {tp}')


# ---------------------------------------------------------------- render
def load_translations(path):
    if path.endswith('.json'):
        j = json.load(open(path)); return j.get('T', j), j.get('COMMON', {})
    spec = importlib.util.spec_from_file_location('figtrans', path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m.T, getattr(m, 'COMMON', {})


CJK_CLASS = '⺀-鿿＀-￯　-〿“”‘’—…·★→'
NOSTART = set('，。；：、）」』”’！？%》,.;:!?)')
CLS = {'r': (0xc2, 0x3b, 0x2a), 'g': (0x1e, 0x8a, 0x52), 'y': (0x8a, 0x64, 0x00)}


def hexrgb(h):
    h = int(h, 16) if isinstance(h, str) else h
    return ((h >> 16) & 255) / 255, ((h >> 8) & 255) / 255, (h & 255) / 255


def parse_runs(txt, bold0, color0, classes):
    """HTML 子集 <b> <b class=r> <i> <span style=color:#xxxxxx> → [(text, bold, color)]"""
    runs, stack = [], [(bold0, color0)]
    for tok in re.split(r'(<[^>]+>)', txt):
        if not tok: continue
        m = re.match(r'<(/?)(\w+)([^>]*)>', tok)
        if m:
            close, tag, attr = m.groups()
            if close:
                if len(stack) > 1: stack.pop()
            else:
                b, c = stack[-1]
                if tag == 'b': b = True
                cm = re.search(r'class=(\w+)', attr)
                if cm and cm.group(1) in classes: c = tuple(v / 255 for v in classes[cm.group(1)])
                hm = re.search(r'#([0-9a-fA-F]{6})', attr)
                if hm: c = hexrgb(hm.group(1))
                stack.append((b, c))
            continue
        runs.append((tok, *stack[-1]))
    return runs


def cmd_render(cfg, a):
    units = {u['id']: u for u in json.load(open(os.path.join(cfg['work_dir'], 'units.json')))}
    T, COMMON = load_translations(cfg['translations'])
    FF = font_files(cfg)
    FONT = {k: pymupdf.Font(fontfile=v) for k, v in FF.items()}
    FN = {False: 'zhreg', True: 'zhbold'}
    classes = dict(CLS); classes.update({k: tuple(v) for k, v in cfg.get('color_classes', {}).items()})
    tok_re = re.compile(rf'[{CJK_CLASS}]|\s+|[^\s{CJK_CLASS}]+')

    def width(t, b, s): return FONT[b].text_length(t, fontsize=s)

    def layout(runs, s, W):
        lines, w = [[]], 0
        for t0, b, c in runs:
            for m in tok_re.finditer(t0):
                t = m.group(0); tw = width(t, b, s)
                if t.isspace():
                    if lines[-1]: lines[-1].append((t, b, c)); w += tw
                    continue
                if w + tw > W + 0.01 and lines[-1] and t not in NOSTART:
                    while lines[-1] and lines[-1][-1][0].isspace(): w -= width(*lines[-1].pop()[:2], s)
                    lines.append([]); w = 0
                lines[-1].append((t, b, c)); w += tw
        for ln in lines:
            while ln and ln[-1][0].isspace(): ln.pop()
        return [ln for ln in lines if ln]

    jobs, used = [], set()
    for k, v in T.items():
        ids = k.split('+')
        miss = [i for i in ids if i not in units]
        if miss: print('警告：翻译表中的 id 不存在', miss, file=sys.stderr); continue
        jobs.append((ids, v)); used.update(ids)
    for uid, u in units.items():
        if uid not in used and u['text'].strip() in COMMON:
            jobs.append(([uid], COMMON[u['text'].strip()]))

    def auto_align(u):
        for r in cfg['auto_align_rules']:
            if r.get('figs') and u['fig'] not in r['figs']: continue
            if 'size' in r and abs(u['size'] - r['size']) > r.get('tol', 0.15): continue
            if r.get('not_color') and u['color'] == r['not_color']: continue
            if 'x0_lt' in r:
                return r['align_if'] if u['x0'] < r['x0_lt'] else r.get('align_else', 'l')
            return r['align_if']
        return 'l'

    doc = pymupdf.open(cfg['source_pdf'])
    redacts, plan = {}, []
    for ids, txt in jobs:
        us = [units[i] for i in ids]; u = us[0]
        rect = pymupdf.Rect(u['bbox'])
        for x in us[1:]: rect |= x['bbox']
        # 对齐前缀：'|' 居中 '>' 右 '<' 左；注意 '<b>' 这类标签开头不是前缀
        if txt[:1] in '|>' or (txt[:1] == '<' and not re.match(r'<[a-z/]', txt)):
            align = {'|': 'c', '<': 'l', '>': 'r'}[txt[0]]; txt = txt[1:]
        else:
            align = auto_align(u)
        for x in us:
            for bb, _ in x['spans']: redacts.setdefault(x['page'], []).append(pymupdf.Rect(bb))
        plan.append(dict(page=u['page'], rect=rect, txt=txt, align=align, s=u['size'],
                         color=hexrgb(u['color']), bold='Bold' in u['font'], dir=u['dir'],
                         origins=[o for x in us for o in x['origins']], multi=len(us) > 1, id='+'.join(ids)))

    # 1) 只删除文字，不动图片和矢量图形（redact 区域上下各收缩 30% 以免误删相邻行）
    for pn, rs in redacts.items():
        p = doc[pn - 1]
        for r in rs:
            h, w = r.height, r.width
            rr = (pymupdf.Rect(r.x0 + .05, r.y0 + h * .3, r.x1 - .05, r.y1 - h * .3) if w > h
                  else pymupdf.Rect(r.x0 + w * .3, r.y0 + .05, r.x1 - w * .3, r.y1 - .05))
            p.add_redact_annot(rr, fill=False)
        p.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                           graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                           text=pymupdf.PDF_REDACT_TEXT_REMOVE)

    # 2) 每页只嵌入一次字体，用 insert_text 自行排版（insert_htmlbox 每次调用都会嵌入字体子集，体积暴涨）
    fonts_on, shrunk = set(), []
    for it in plan:
        p = doc[it['page'] - 1]
        for b in (False, True):
            if (it['page'], b) not in fonts_on:
                p.insert_font(fontname=FN[b], fontfile=FF[b]); fonts_on.add((it['page'], b))
        r, s0, nl = it['rect'], it['s'], len(it['origins'])
        runs = parse_runs(it['txt'], it['bold'], it['color'], classes)
        rot = it['dir'][1] < -0.5
        o0 = it['origins'][0]
        pitch = (it['origins'][-1][1] - o0[1]) / (nl - 1) if nl > 1 and not rot else s0 * 1.25
        if rot: W, maxl = max(r.height * 1.4, r.height + 6), 1
        elif nl > 1 and not it['multi']: W, maxl = r.width + 1.0, nl
        elif it['multi']: W, maxl = r.width + (8 if it['align'] == 'c' else 2), nl
        else: W, maxl = {'c': r.width * 1.3 + 6, 'r': r.width + 25, 'l': r.width + 2}[it['align']], 1
        s = s0
        while True:
            lines = layout(runs, s, W)
            if len(lines) <= maxl or s < s0 * 0.6: break
            s *= 0.96
        if s < s0 - 1e-6: shrunk.append((it['id'], round(s / s0, 2), it['txt'][:24]))
        for i, ln in enumerate(lines):
            w = sum(width(t, b, s) for t, b, c in ln)
            if rot:   # 竖排（自下而上）：保持原基线 x，沿 y 居中
                x, y = o0[0], (r.y0 + r.y1) / 2 + w / 2
                for t, b, c in ln:
                    p.insert_text((x, y), t, fontsize=s, fontname=FN[b], color=c, rotate=90)
                    y -= width(t, b, s)
                continue
            base = o0[1] + ((nl - len(lines)) * pitch / 2 if it['multi'] and len(lines) < nl else 0) + i * pitch
            x = {'l': o0[0] if not it['multi'] else r.x0, 'r': r.x1 - w, 'c': (r.x0 + r.x1) / 2 - w / 2}[it['align']]
            for t, b, c in ln:
                p.insert_text((x, base), t, fontsize=s, fontname=FN[b], color=c)
                x += width(t, b, s)
    out = os.path.join(cfg['work_dir'], 'figs_zh.pdf')
    doc.subset_fonts(); doc.save(out, garbage=3, deflate=True)
    print(f'{len(plan)} 个单元已写入 → {out}')
    if shrunk:
        print('以下单元为放入原区域而缩小了字号（可缩短译文或用 A+B 合并区域）：')
        for x in shrunk: print('  ', x)
    if a.preview:
        pages = sorted({f['page'] for f in cfg['figures'] if f.get('translate')})
        d2 = pymupdf.open(out)
        for pn in pages:
            fp = os.path.join(cfg['work_dir'], f'figzh_p{pn}.png')
            d2[pn - 1].get_pixmap(dpi=a.dpi).save(fp); print('preview', fp)


# ---------------------------------------------------------------- crop
def cmd_crop(cfg, a):
    zh = os.path.join(cfg['work_dir'], 'figs_zh.pdf')
    out = pymupdf.open()
    os.makedirs(cfg['latex_dir'], exist_ok=True)
    for i, fg in enumerate(cfg['figures']):
        src = zh if fg.get('translate') else cfg['source_pdf']
        if fg.get('translate') and not os.path.exists(zh): sys.exit('先运行 render')
        d = pymupdf.open(src); p = d[fg['page'] - 1]
        x0, y0, x1, y1 = fg['rect']; W, H = p.rect.width, p.rect.height
        # 删掉裁剪区外的文字与图形，避免成品里残留不可见的英文文字层
        for rr in [(0, 0, W, y0), (0, y1, W, H), (0, y0, x0, y1), (x1, y0, W, y1)]:
            p.add_redact_annot(pymupdf.Rect(rr), fill=False)
        p.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                           graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                           text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        for l in p.get_links(): p.delete_link(l)
        p.set_cropbox(pymupdf.Rect(fg['rect']))
        out.insert_pdf(d, from_page=fg['page'] - 1, to_page=fg['page'] - 1)
        print(f'page {i + 1:2d}  {fg["name"]:10s} {"zh" if fg.get("translate") else "orig"}  '
              f'{x1 - x0:.0f}x{y1 - y0:.0f}pt')
    fp = os.path.join(cfg['latex_dir'], 'figs.pdf')
    out.save(fp, garbage=4, deflate=True)
    print('→', fp, '（LaTeX 中用 \\fig{页号} 引用，页号即上面的序号）')


# ---------------------------------------------------------------- build / preview
def cmd_build(cfg, a):
    main = cfg.get('main_tex', 'main.tex')
    r = subprocess.run(['tectonic', '--keep-logs', main], cwd=cfg['latex_dir'], capture_output=True, text=True)
    log = r.stdout + r.stderr
    for line in log.splitlines():
        if re.search(r'error|Overfull|undefined', line, re.I) and 'accessing absolute path' not in line:
            print(line)
    if r.returncode: sys.exit('编译失败，见 ' + os.path.join(cfg['latex_dir'], 'main.log'))
    pdf = os.path.join(cfg['latex_dir'], os.path.splitext(main)[0] + '.pdf')
    if cfg.get('output_pdf'):
        shutil.copy(pdf, cfg['output_pdf']); pdf = cfg['output_pdf']
    print('OK →', pdf, f'({pymupdf.open(pdf).page_count} 页)')


def cmd_preview(cfg, a):
    from PIL import Image
    pdf = a.pdf or cfg.get('output_pdf') or os.path.join(cfg['latex_dir'], 'main.pdf')
    d = pymupdf.open(pdf); n = len(d); per = a.cols * a.rows
    for k in range(0, n, per):
        ims = []
        for i in range(k, min(k + per, n)):
            pix = d[i].get_pixmap(dpi=a.dpi)
            ims.append(Image.frombytes('RGB', (pix.width, pix.height), pix.samples))
        w, h = ims[0].size
        sheet = Image.new('RGB', (w * a.cols, h * a.rows), 'white')
        for i, im in enumerate(ims): sheet.paste(im, ((i % a.cols) * w, (i // a.cols) * h))
        fp = os.path.join(cfg['work_dir'], f'sheet_{k // per + 1:02d}.png'); sheet.save(fp); print(fp)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-c', '--config', default='project.json')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('fonts')
    s = sub.add_parser('inspect')
    s.add_argument('--pages'); s.add_argument('--mode', default='blocks', choices=['blocks', 'fonts', 'colors', 'bold', 'all'])
    s.add_argument('--y', nargs=2, type=float, help='bold 模式下只看该纵坐标范围')
    s.add_argument('--crop', nargs=5, type=float, metavar=('PAGE', 'X0', 'Y0', 'X1', 'Y1'))
    s.add_argument('--zoom', type=float, default=3)
    s = sub.add_parser('regions'); s.add_argument('--pages'); s.add_argument('--gap', type=float, default=12)
    sub.add_parser('extract')
    s = sub.add_parser('render'); s.add_argument('--preview', action='store_true'); s.add_argument('--dpi', type=int, default=150)
    sub.add_parser('crop')
    sub.add_parser('build')
    s = sub.add_parser('preview'); s.add_argument('--pdf'); s.add_argument('--dpi', type=int, default=40)
    s.add_argument('--cols', type=int, default=4); s.add_argument('--rows', type=int, default=2)
    a = ap.parse_args()
    cfg = load_cfg(a.config)
    globals()['cmd_' + a.cmd](cfg, a)


if __name__ == '__main__':
    main()
