"""溯源翻译：从 arXiv LaTeX 源码翻译（pdftr 的 source 模式）。

流程：fetch(下载源码) → prepare(注入中文支持 + 切块) → translate(agent 并行 / API 并行)
      → check(结构/数字/残留英文校验) → assemble(回填 + 可选中英对照) → build(tectonic)
"""
import concurrent.futures as cf
import gzip, io, json, os, re, shutil, subprocess, sys, tarfile, time, urllib.request
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
UA = {'User-Agent': 'pdftr/1.0 (paper-translate-zh)'}

ARXIV_NEW = r'(\d{4}\.\d{4,5})(v\d+)?'
ARXIV_OLD = r'([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?'


# ================================================================ 识别 arXiv ID
def arxiv_id_from_text(s):
    s = s.strip()
    m = re.search(r'arxiv\.org/(?:abs|pdf|e-print|html)/(' + ARXIV_NEW + '|' + ARXIV_OLD + ')', s, re.I)
    if m: return m.group(1).removesuffix('.pdf')
    m = re.search(r'arXiv:\s*(' + ARXIV_NEW + '|' + ARXIV_OLD + ')', s, re.I)
    if m: return m.group(1)
    m = re.fullmatch(ARXIV_NEW + '|' + ARXIV_OLD, s)
    return s if m else None


def arxiv_id_from_pdf(path):
    """本地 PDF → arXiv ID（读侧边水印 / 首页文字 / 元数据 / 文件名）。"""
    try:
        import pymupdf
        d = pymupdf.open(path)
        cands = [d[0].get_text()] if len(d) else []
        cands += [str(v) for v in (d.metadata or {}).values() if v]
    except Exception:
        cands = []
    cands.append(os.path.basename(path).removesuffix('.pdf'))
    for c in cands:
        m = re.search(r'arXiv:\s*(' + ARXIV_NEW + '|' + ARXIV_OLD + ')', c, re.I)
        if m: return m.group(1)
    m = re.fullmatch(ARXIV_NEW, cands[-1])
    return cands[-1] if m else None


def resolve_input(arg):
    """返回 (arxiv_id 或 None, 本地 pdf 路径或 None)"""
    if os.path.exists(arg):
        return (arxiv_id_from_pdf(arg) if arg.lower().endswith('.pdf') else None), os.path.abspath(arg)
    return arxiv_id_from_text(arg), None


def _get(url, timeout=120):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers.get('Content-Type', '')


# ================================================================ fetch
def fetch(aid, proj):
    """下载 arXiv 源码到 proj/src，原 PDF 到 proj/orig.pdf。无源码（仅 PDF 投稿）时返回 False。"""
    os.makedirs(proj, exist_ok=True)
    src = os.path.join(proj, 'src')
    pdf = os.path.join(proj, 'orig.pdf')
    if not os.path.exists(pdf):
        data, _ = _get(f'https://arxiv.org/pdf/{aid}')
        open(pdf, 'wb').write(data)
    if os.path.isdir(src) and os.listdir(src):
        return True
    data, ctype = _get(f'https://arxiv.org/e-print/{aid}')
    os.makedirs(src, exist_ok=True)
    if data[:4] == b'%PDF' or 'pdf' in ctype:
        shutil.rmtree(src); return False          # 作者只上传了 PDF
    raw = gzip.decompress(data) if data[:2] == b'\x1f\x8b' else data
    try:
        with tarfile.open(fileobj=io.BytesIO(raw)) as t:
            t.extractall(src, filter='data')
    except tarfile.ReadError:                       # 单个 .tex 文件
        open(os.path.join(src, 'main.tex'), 'wb').write(raw)
    return True


# ================================================================ LaTeX 解析小工具
def strip_comment(line):
    """去掉行内注释（忽略 \\%）。"""
    return re.sub(r'(?<!\\)%.*', '', line)


def find_main(srcdir):
    best = None
    for root, _, files in os.walk(srcdir):
        for f in files:
            if not f.endswith('.tex'): continue
            p = os.path.join(root, f)
            t = open(p, errors='ignore').read()
            body = '\n'.join(strip_comment(l) for l in t.splitlines())
            if re.search(r'\\documentclass', body) and '\\begin{document}' in body:
                score = (f.removesuffix('.tex') in ('main', 'ms', 'paper', 'article'), len(t))
                if best is None or score > best[0]: best = (score, p)
    if not best: sys.exit('源码中找不到包含 \\documentclass 和 \\begin{document} 的主文件')
    return best[1]


def resolve_tex(basedir, name):
    for cand in (name, name + '.tex'):
        p = os.path.normpath(os.path.join(basedir, cand))
        if os.path.isfile(p): return p
    return None


INCLUDE_RE = re.compile(r'\\(?:input|include|subfile)\s*\{([^}]+)\}')


def included_files(main):
    """主文件递归 \\input/\\include 的 .tex 文件（相对主文件目录解析），按出现顺序。"""
    base = os.path.dirname(main); seen = []; stack = [main]
    while stack:
        f = stack.pop(0)
        if f in seen: continue
        seen.append(f)
        txt = '\n'.join(strip_comment(l) for l in open(f, errors='ignore').read().splitlines())
        for m in INCLUDE_RE.finditer(txt):
            p = resolve_tex(base, m.group(1).strip())
            if p and p.endswith('.tex'): stack.append(p)
    return seen


# ================================================================ 预处理（XeLaTeX 兼容 + 中文支持）
CJK_PREAMBLE = r'''
%% ---- pdftr: 中文支持（自动注入） ----
\usepackage[UTF8,scheme=plain,punct=quanjiao,fontset=%(fontset)s]{ctex}
\usepackage{xcolor}
\newcommand{\pdftrEN}[1]{{\color{black!55}\small #1\par}}
\AtBeginDocument{\renewcommand{\figurename}{图}\renewcommand{\tablename}{表}%%
  \providecommand{\abstractname}{}\renewcommand{\abstractname}{摘要}%%
  \renewcommand{\refname}{参考文献}\providecommand{\bibname}{}\renewcommand{\bibname}{参考文献}}
%% ---- pdftr end ----
'''


def sanitize(text):
    """去掉 pdfLaTeX 专属、在 XeTeX 下会报错的写法。"""
    text = re.sub(r'^[ \t]*\\pdf(output|minorversion|compresslevel|objcompresslevel)\b.*$', r'% [pdftr] \g<0>', text, flags=re.M)
    text = re.sub(r'\\usepackage\[([^\]]*)\]\{graphicx\}',
                  lambda m: '\\usepackage[' + ','.join(o for o in m.group(1).split(',') if o.strip() not in ('pdftex', 'dvips')) + ']{graphicx}', text)
    text = re.sub(r'^[ \t]*\\usepackage(\[[^\]]*\])?\{(pdfsync|epstopdf|fontenc|inputenc)\}.*$', r'% [pdftr] \g<0>', text, flags=re.M)
    return text


TIMES_FONTS = r'''
%% ---- pdftr: XeLaTeX 下用 TeX Gyre（与 Times/Helvetica/Courier 同款字形）代替 ptm/phv/pcr ----
\setmainfont{texgyretermes}[Extension=.otf,UprightFont=*-regular,BoldFont=*-bold,ItalicFont=*-italic,BoldItalicFont=*-bolditalic]
\setsansfont{texgyreheros}[Extension=.otf,UprightFont=*-regular,BoldFont=*-bold,ItalicFont=*-italic,BoldItalicFont=*-bolditalic]
\setmonofont{texgyrecursor}[Extension=.otf,UprightFont=*-regular,BoldFont=*-bold,ItalicFont=*-italic,BoldItalicFont=*-bolditalic]
'''


def uses_times(srcdir, preamble):
    txt = preamble
    for root, _, files in os.walk(srcdir):
        for f in files:
            if f.endswith(('.sty', '.cls')): txt += open(os.path.join(root, f), errors='ignore').read()
    return bool(re.search(r'\\usepackage(\[[^\]]*\])?\{[^}]*\b(times|mathptmx|newtxtext|txfonts|ptm)\b|\\rmdefault\}?\{ptm\}', txt))


def drop_comment_lines(text):
    """删除整行注释（verbatim 类环境之外），减少翻译量。"""
    out, verb = [], 0
    for l in text.split('\n'):
        if re.search(r'\\begin\{(verbatim|lstlisting|minted|comment)\*?\}', l): verb += 1
        if re.search(r'\\end\{(verbatim|lstlisting|minted|comment)\*?\}', l): verb -= 1
        if verb == 0 and re.match(r'^\s*%', l) and not l.lstrip().startswith('%%'):
            continue
        out.append(l)
    return '\n'.join(out)


def default_fontset():
    return 'mac' if sys.platform == 'darwin' else 'fandol'


# ================================================================ 切块
SKIP_ENVS = ('thebibliography',)          # 不翻译的整段环境
ENV_RE = re.compile(r'\\(begin|end)\s*\{([^}]+)\}')


def segments_to_chunk(text, start, end, target):
    """把 text[start:end] 在"环境深度为 0 的空行"处切块，返回 [(s,e)]，跳过 SKIP_ENVS。"""
    seg = text[start:end]
    # 先挖掉不翻译的环境
    holes = []
    for env in SKIP_ENVS:
        for m in re.finditer(r'\\begin\{' + env + r'\}.*?\\end\{' + env + r'\}', seg, re.S):
            holes.append((m.start(), m.end()))
    pieces, cur = [], 0
    for hs, he in sorted(holes):
        pieces.append((cur, hs)); cur = he
    pieces.append((cur, len(seg)))
    out = []
    for ps, pe in pieces:
        part = seg[ps:pe]
        depth, last_cut, pos = 0, 0, 0
        cuts = []
        for line in part.splitlines(keepends=True):
            code = strip_comment(line)
            for m in ENV_RE.finditer(code):
                depth += 1 if m.group(1) == 'begin' else -1
            pos += len(line)
            if depth <= 0 and line.strip() == '' and pos - last_cut >= target:
                cuts.append((last_cut, pos)); last_cut = pos; depth = 0
        if last_cut < len(part): cuts.append((last_cut, len(part)))
        for s, e in cuts:
            if prose_english(part[s:e])[0] >= 2:
                out.append((start + ps + s, start + ps + e))
    return out


def find_braced(text, pos):
    """text[pos] == '{'，返回配对 '}' 之后的位置。"""
    depth = 0
    for i in range(pos, len(text)):
        c = text[i]
        if c == '\\': continue
        if c == '{' and text[i - 1] != '\\': depth += 1
        elif c == '}' and text[i - 1] != '\\':
            depth -= 1
            if depth == 0: return i + 1
    return len(text)


def prepare(proj, target=3500, fontset=None):
    src, zh = os.path.join(proj, 'src'), os.path.join(proj, 'zh')
    work = os.path.join(proj, 'work')
    if os.path.exists(zh): shutil.rmtree(zh)
    shutil.copytree(src, zh)
    main = find_main(zh)
    files = included_files(main)
    for f in files:
        t = open(f, errors='ignore').read()
        t = drop_comment_lines(sanitize(t))
        open(f, 'w').write(t)
    # 会议模板 .sty/.cls 里写死的 "Abstract" 标题 → \abstractname（已被改成"摘要"）
    for root, _, fs in os.walk(zh):
        for fn in fs:
            if fn.endswith(('.sty', '.cls')):
                fp = os.path.join(root, fn); t = open(fp, errors='ignore').read()
                t2 = re.sub(r'(\\bf|\\bfseries|\\textbf\{|\\scshape|\\sc)(\s*)Abstract\b', r'\1\2\\abstractname', t)
                if t2 != t: open(fp, 'w').write(t2)
    # 注入中文支持（\documentclass 之后）
    mt = open(main).read()
    m = re.search(r'\\documentclass(\[[^\]]*\])?\{[^}]+\}[^\n]*\n', mt)
    mt = mt[:m.end()] + CJK_PREAMBLE % {'fontset': fontset or default_fontset()} + mt[m.end():]
    b = mt.find('\\begin{document}')
    if uses_times(zh, mt[:b]):
        mt = mt[:b] + TIMES_FONTS + mt[b:]
    open(main, 'w').write(mt)

    chunks = []
    for f in files:
        t = open(f).read()
        if f == main:
            b = t.find('\\begin{document}')
            # 导言区里的标题、作者（含 \thanks 脚注）单独成块
            for cmd in ('title', 'author'):
                tm = re.search(r'\\' + cmd + r'\s*(\[[^\]]*\])?\s*\{', t[:b])
                if tm:
                    chunks.append((f, tm.start(), find_braced(t, tm.end() - 1)))
            e_doc = t.find('\\end{document}')
            spans = segments_to_chunk(t, b + len('\\begin{document}'), e_doc if e_doc > 0 else len(t), target)
        else:
            spans = segments_to_chunk(t, 0, len(t), target)
        chunks += [(f, s, e) for s, e in spans]

    cdir = os.path.join(work, 'chunks')
    keep = {}                      # 重新切块时，按原文内容保留已有译文
    if os.path.isdir(os.path.join(cdir, 'zh')):
        for fn in os.listdir(os.path.join(cdir, 'zh')):
            sp = os.path.join(cdir, 'src', fn)
            if os.path.exists(sp): keep[open(sp).read()] = open(os.path.join(cdir, 'zh', fn)).read()
    if os.path.exists(cdir): shutil.rmtree(cdir)
    os.makedirs(os.path.join(cdir, 'src')); os.makedirs(os.path.join(cdir, 'zh'))
    manifest = []
    for i, (f, s, e) in enumerate(chunks, 1):
        cid = f'{i:03d}'
        body = open(f).read()[s:e]
        open(os.path.join(cdir, 'src', cid + '.tex'), 'w').write(body)
        if body in keep: open(os.path.join(cdir, 'zh', cid + '.tex'), 'w').write(keep[body])
        manifest.append(dict(id=cid, file=os.path.relpath(f, zh), start=s, end=e, chars=len(body)))
    meta = dict(main=os.path.relpath(main, zh), chunks=manifest)
    json.dump(meta, open(os.path.join(work, 'manifest.json'), 'w'), indent=1, ensure_ascii=False)
    write_term_candidates(cdir, os.path.join(work, 'terms.txt'))
    gl = os.path.join(work, 'glossary.md')
    if not os.path.exists(gl):
        shutil.copy(os.path.join(SKILL_ROOT, 'templates', 'glossary.example.md'), gl)
    return meta


def write_term_candidates(cdir, out):
    """统计高频术语候选：缩写词与多词大写短语、高频小写双词短语。"""
    txt = ''
    for f in sorted(os.listdir(os.path.join(cdir, 'src'))):
        t = open(os.path.join(cdir, 'src', f)).read()
        t = re.sub(r'\$[^$]*\$|\\[a-zA-Z]+\*?(\[[^\]]*\])?(\{[^{}]*\})?', ' ', t)
        txt += t + '\n'
    acr = Counter(re.findall(r'\b[A-Z][A-Za-z]*[A-Z][A-Za-z0-9\-]*\b', txt))
    caps = Counter(re.findall(r'\b(?:[A-Z][a-z]+(?:[- ][A-Z][a-z]+)+)\b', txt))
    words = re.findall(r'\b[a-z][a-z\-]{3,}\b', txt.lower())
    stop = set('this that with from have been were which their these those such also into than then more most only other some when where while there them they over each both used using between based'.split())
    bi = Counter(f'{a} {b}' for a, b in zip(words, words[1:]) if a not in stop and b not in stop)
    with open(out, 'w') as f:
        f.write('# 术语候选（自动统计，供制定 glossary.md 参考）\n\n## 缩写 / 专名\n')
        for w, n in acr.most_common(40):
            if n >= 2: f.write(f'{w}\t{n}\n')
        f.write('\n## 大写短语\n')
        for w, n in caps.most_common(30):
            if n >= 2: f.write(f'{w}\t{n}\n')
        f.write('\n## 高频双词短语\n')
        for w, n in bi.most_common(40):
            if n >= 3: f.write(f'{w}\t{n}\n')


def batches(meta, n):
    """按字数把块均衡分给 n 个 agent（贪心）。"""
    bins = [[0, []] for _ in range(n)]
    for c in sorted(meta['chunks'], key=lambda c: -c['chars']):
        b = min(bins, key=lambda x: x[0]); b[0] += c['chars']; b[1].append(c['id'])
    return [sorted(b[1]) for b in bins if b[1]]


# ================================================================ 翻译（API 并行引擎）
def build_prompt(glossary, chunk):
    tpl = open(os.path.join(SKILL_ROOT, 'templates', 'translate_prompt.md')).read()
    return tpl.replace('{{GLOSSARY}}', glossary.strip() or '（无）').replace('{{CHUNK}}', chunk)


def call_api(prompt, timeout=600):
    if os.environ.get('ANTHROPIC_API_KEY') and not os.environ.get('PDFTR_API_BASE'):
        body = {'model': os.environ.get('PDFTR_MODEL', 'claude-sonnet-5'), 'max_tokens': 16000,
                'messages': [{'role': 'user', 'content': prompt}]}
        req = urllib.request.Request('https://api.anthropic.com/v1/messages', data=json.dumps(body).encode(),
              headers={'x-api-key': os.environ['ANTHROPIC_API_KEY'], 'anthropic-version': '2023-06-01',
                       'content-type': 'application/json'})
        r = json.load(urllib.request.urlopen(req, timeout=timeout))
        return ''.join(b.get('text', '') for b in r['content'])
    base = os.environ.get('PDFTR_API_BASE') or os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1')
    key = os.environ.get('PDFTR_API_KEY') or os.environ.get('OPENAI_API_KEY')
    if not key: sys.exit('API 引擎需要 PDFTR_API_KEY / OPENAI_API_KEY 或 ANTHROPIC_API_KEY')
    body = {'model': os.environ.get('PDFTR_MODEL', 'gpt-4o-mini'), 'temperature': 0,
            'messages': [{'role': 'user', 'content': prompt}]}
    req = urllib.request.Request(base.rstrip('/') + '/chat/completions', data=json.dumps(body).encode(),
          headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    r = json.load(urllib.request.urlopen(req, timeout=timeout))
    return r['choices'][0]['message']['content']


def unwrap(out):
    m = re.search(r'```(?:latex|tex)?\n(.*?)```', out, re.S)
    return m.group(1) if m else out


def translate_api(proj, workers=8, only=None):
    work = os.path.join(proj, 'work'); cdir = os.path.join(work, 'chunks')
    meta = json.load(open(os.path.join(work, 'manifest.json')))
    gl = open(os.path.join(work, 'glossary.md')).read()
    todo = [c['id'] for c in meta['chunks']
            if (not only or c['id'] in only) and not os.path.exists(os.path.join(cdir, 'zh', c['id'] + '.tex'))]
    t0 = time.time()

    def one(cid):
        src = open(os.path.join(cdir, 'src', cid + '.tex')).read()
        for attempt in range(3):
            try:
                out = unwrap(call_api(build_prompt(gl, src)))
                open(os.path.join(cdir, 'zh', cid + '.tex'), 'w').write(out)
                return cid, None
            except Exception as e:
                err = e; time.sleep(2 * (attempt + 1))
        return cid, err

    with cf.ThreadPoolExecutor(workers) as ex:
        for cid, err in ex.map(one, todo):
            print(f'  {cid} {"OK" if err is None else "FAIL " + str(err)[:80]}')
    print(f'{len(todo)} 块，{workers} 路并行，用时 {time.time() - t0:.0f}s')


# ================================================================ 翻译（Codex CLI 并行引擎）
def translate_codex(proj, agents=6, model=None, only=None):
    """并发启动多个 `codex exec`，每个进程翻译一组块（按字数均衡）。"""
    exe = shutil.which('codex')
    if not exe: sys.exit('未找到 codex 命令（npm i -g @openai/codex 或 brew install codex）')
    proj = os.path.abspath(proj)
    work = os.path.join(proj, 'work'); cdir = os.path.join(work, 'chunks')
    meta = json.load(open(os.path.join(work, 'manifest.json')))
    todo = {c['id'] for c in meta['chunks']
            if (not only or c['id'] in only) and not os.path.exists(os.path.join(cdir, 'zh', c['id'] + '.tex'))}
    sub = dict(meta, chunks=[c for c in meta['chunks'] if c['id'] in todo])
    groups = batches(sub, agents)
    if not groups: print('没有待翻译的块'); return
    rules = open(os.path.join(SKILL_ROOT, 'templates', 'translate_prompt.md')).read().split('## 术语表')[0]
    gl = open(os.path.join(work, 'glossary.md')).read()
    logs = os.path.join(work, 'codex_logs'); os.makedirs(logs, exist_ok=True)
    t0 = time.time()

    def run(i, ids):
        files = '\n'.join(f'- work/chunks/src/{x}.tex  →  work/chunks/zh/{x}.tex' for x in ids)
        prompt = (f"你是论文并行翻译中的一个子 agent。当前目录是翻译工程。\n\n{rules}\n## 术语表\n\n{gl}\n\n"
                  f"## 你的任务\n逐个读取下列源文件，按上面的规则翻译，把译文（只有 LaTeX，不要代码块和说明）写到对应路径：\n{files}\n"
                  f"写完后运行 `{SKILL_ROOT}/pdftr check {proj}`，只修你自己负责的块的问题。不要改其他文件。")
        cmd = [exe, 'exec', '--skip-git-repo-check', '--sandbox', 'workspace-write', '--ephemeral',
               '-C', proj, '-o', os.path.join(logs, f'agent{i}.txt')]
        if model: cmd += ['-m', model]
        r = subprocess.run(cmd + ['-'], input=prompt, capture_output=True, text=True)
        done = [x for x in ids if os.path.exists(os.path.join(cdir, 'zh', x + '.tex'))]
        return i, ids, done, r.returncode

    with cf.ThreadPoolExecutor(len(groups)) as ex:
        futs = [ex.submit(run, i, g) for i, g in enumerate(groups, 1)]
        for f in cf.as_completed(futs):
            i, ids, done, rc = f.result()
            print(f'  codex agent {i}: {len(done)}/{len(ids)} 块完成{"" if rc == 0 else f"（退出码 {rc}，日志见 work/codex_logs/agent{i}.txt）"}')
    print(f'{len(todo)} 块，{len(groups)} 个 codex 进程并行，用时 {time.time() - t0:.0f}s')


# ================================================================ 校验
CMD_KEYS = r'\\(cite[pt]?|citep|citet|ref|eqref|autoref|cref|Cref|label|url|href|footnote|item|caption|section|subsection|subsubsection|paragraph)\b'


def strip_math(t):
    t = re.sub(r'\\begin\{(equation|align|gather|multline|eqnarray|displaymath)\*?\}.*?\\end\{\1\*?\}', ' ', t, flags=re.S)
    t = re.sub(r'\\\[.*?\\\]|\$\$.*?\$\$|\$[^$]*\$|\\\(.*?\\\)', ' ', t, flags=re.S)
    return t


def signature(t):
    sig = Counter(m.group(0) for m in re.finditer(CMD_KEYS, t))
    for m in ENV_RE.finditer(t): sig[f'\\{m.group(1)}{{{m.group(2)}}}'] += 1
    sig['$'] = len(re.findall(r'(?<!\\)\$', t))
    sig['{'] = t.count('{') - t.count('\\{'); sig['}'] = t.count('}') - t.count('\\}')
    for m in re.finditer(r'\\(?:cite[pt]?|ref|eqref|label)\{([^}]*)\}', t): sig['key:' + m.group(1)] += 1
    return sig


def numbers(t):
    # "4.5 million / 2 billion" 允许换算成"450 万 / 20 亿"，不纳入逐字核对
    t = re.sub(r'\d+(?:\.\d+)?\s*(?:million|billion|thousand)\b', ' ', t, flags=re.I)
    return Counter(re.findall(r'(?<![A-Za-z0-9_.])\d+(?:[.,]\d+)*(?![A-Za-z0-9_])', t))


def prose_english(t):
    """去掉命令和数学后的英文单词数、中文字数。"""
    t = strip_math(t)
    t = re.sub(r'\\(cite[pt]?|ref|eqref|label|url|href|includegraphics|begin|end|input|bibliography\w*)\*?(\[[^\]]*\])?\{[^}]*\}', ' ', t)
    t = re.sub(r'\\[a-zA-Z]+\*?', ' ', t)
    en = len(re.findall(r'[A-Za-z]{3,}', t)); cn = len(re.findall(r'[\u4e00-\u9fff]', t))
    return en, cn


def check(proj, verbose=True):
    work = os.path.join(proj, 'work'); cdir = os.path.join(work, 'chunks')
    meta = json.load(open(os.path.join(work, 'manifest.json')))
    problems = {}; missing = []
    for c in meta['chunks']:
        zp = os.path.join(cdir, 'zh', c['id'] + '.tex')
        if not os.path.exists(zp): missing.append(c['id']); continue
        s, z = open(os.path.join(cdir, 'src', c['id'] + '.tex')).read(), open(zp).read()
        msgs = []
        d = signature(s); d.subtract(signature(z))
        diff = {k: v for k, v in d.items() if v}
        if diff: msgs.append('结构不一致（正=译文缺少，负=译文多出）: ' + ', '.join(f'{k}:{v:+d}' for k, v in sorted(diff.items())))
        dn = numbers(s); dn.subtract(numbers(z))
        nd = {k: v for k, v in dn.items() if v > 0}
        if nd: msgs.append('译文缺少数字: ' + ', '.join(f'{k}×{v}' for k, v in nd.items()))
        en, cn = prose_english(z); en0, _ = prose_english(s)
        if en0 >= 15 and cn < en0 * 0.8:
            msgs.append(f'疑似未翻译：原文英文词 {en0}，译文中文字 {cn}、英文词 {en}')
        if msgs: problems[c['id']] = msgs
    if verbose:
        print(f'共 {len(meta["chunks"])} 块：缺译 {len(missing)}，有问题 {len(problems)}')
        if missing: print('  缺译:', ' '.join(missing))
        for cid, ms in problems.items():
            for m in ms: print(f'  [{cid}] {m}')
    return missing, problems


# ================================================================ 回填与编译
def balanced(p):
    d = 0
    for i, c in enumerate(p):
        if c in '{}' and (i == 0 or p[i - 1] != '\\'):
            d += 1 if c == '{' else -1
            if d < 0: return False
    return d == 0


def is_prose(p):
    if not balanced(p) or re.search(r'(?<!\\)%|\\verb|\\(par|newline|linebreak)\b', p): return False
    if re.search(r'\\(begin|end|section|subsection|subsubsection|paragraph|item|caption|maketitle|title)\b|\\\[|\$\$', p): return False
    en, _ = prose_english(p)
    return en >= 6


def bilingual_chunk(src, zh):
    sp = re.split(r'\n\s*\n', src.strip()); zp = re.split(r'\n\s*\n', zh.strip())
    if len(sp) != len(zp): return zh, False
    out = []
    for a, b in zip(sp, zp):
        out.append(f'\\pdftrEN{{{a.strip()}}}\n{b}' if is_prose(a) and '\\footnote' not in a and '\\verb' not in a else b)
    return '\n\n'.join(out) + '\n', True


def assemble(proj, bilingual=False):
    work = os.path.join(proj, 'work'); cdir = os.path.join(work, 'chunks')
    meta = json.load(open(os.path.join(work, 'manifest.json')))
    outs = {'mono': os.path.join(proj, 'out_mono')}
    if bilingual: outs['bi'] = os.path.join(proj, 'out_bi')
    by_file = {}
    for c in meta['chunks']: by_file.setdefault(c['file'], []).append(c)
    stats = {}
    for kind, od in outs.items():
        if os.path.exists(od): shutil.rmtree(od)
        shutil.copytree(os.path.join(proj, 'zh'), od)
        fallback = 0
        for f, cs in by_file.items():
            p = os.path.join(od, f); t = open(p).read()
            for c in sorted(cs, key=lambda c: -c['start']):
                zp = os.path.join(cdir, 'zh', c['id'] + '.tex')
                if not os.path.exists(zp): continue
                z = open(zp).read()
                if kind == 'bi':
                    z, ok = bilingual_chunk(t[c['start']:c['end']], z); fallback += (not ok)
                if not z.endswith('\n'): z += '\n'
                t = t[:c['start']] + z + t[c['end']:]
            open(p, 'w').write(t)
        stats[kind] = fallback
    if bilingual and stats.get('bi'):
        print(f'中英对照：{stats["bi"]} 个块段落数与原文不一致，这些块只放中文')
    return {k: os.path.join(v, meta['main']) for k, v in outs.items()}


def build_tex(main_tex, out_pdf):
    r = subprocess.run(['tectonic', '-Z', 'continue-on-errors', os.path.basename(main_tex)],
                       cwd=os.path.dirname(main_tex), capture_output=True, text=True)
    pdf = main_tex[:-4] + '.pdf'
    errs = [l for l in (r.stdout + r.stderr).splitlines() if re.search(r'^error|! ', l)]
    if not os.path.exists(pdf):
        print('\n'.join(errs[-20:])); sys.exit('编译失败：' + main_tex)
    shutil.copy(pdf, out_pdf)
    if errs: print(f'  （有 {len(errs)} 条 TeX 错误被跳过，成品已生成；详见 tectonic 输出）')
    return out_pdf
