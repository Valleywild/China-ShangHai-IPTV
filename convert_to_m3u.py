#!/usr/bin/env python3
"""
convert_to_m3u.py
将 "频道名,URL" 格式的 txt 文件转换为标准 M3U 格式，
并自动从参考 M3U 文件中提取 tvg-name / tvg-logo 信息。
未在参考文件中找到的频道使用内置补充映射表。

用法:
    python3 convert_to_m3u.py [输入文件] [参考M3U] [输出文件]
默认:
    输入文件  = temp1.txt
    参考M3U   = IPTV_Merged_change.m3u
    输出文件  = temp1_converted.m3u
"""

import re
import sys

# ── 配置 ──────────────────────────────────────────────────────────────────────
INPUT_FILE  = sys.argv[1] if len(sys.argv) > 1 else 'temp1.txt'
REF_M3U     = sys.argv[2] if len(sys.argv) > 2 else 'IPTV_Merged_change.m3u'
OUTPUT_FILE = sys.argv[3] if len(sys.argv) > 3 else 'temp1_converted.m3u'

# ── Logo 库前缀 ───────────────────────────────────────────────────────────────
BASE  = "https://gcore.jsdelivr.net/gh/luoluowoaini/666-@iill-logo-mytvsuper"
BASE2 = "https://gcore.jsdelivr.net/gh/luoluowoaini/666-@main"
TAKS  = "https://gcore.jsdelivr.net/gh/taksssss/tv@main/icon"

# ── 内置补充映射（参考 M3U 中没有对应条目的频道）────────────────────────────
EXTRA_MAP = {
    '/163189/hoy2':     ('HOY77',      f'{BASE}/HOY TV.png'),
    '/163189/hoy78':    ('HOY78',      f'{BASE}/HOY TV.png'),
    '/163189/hoy76':    ('HOY76',      f'{BASE}/HOY TV.png'),
    '/163189/viu':      ('ViuTV',      f'{BASE}/ViuTV.png'),
    '/163189/viu6':     ('ViuTVsix',   f'{BASE}/Viu TV Six.png'),
    '/163189/now':      ('NOW新闻台',   f'{BASE}/Now News.png'),
    '/163189/rthk31':   ('RTHK31',     f'{BASE}/RTHK31.png'),
    '/163189/rthk32':   ('RTHK32',     f'{BASE}/RTHK32.png'),
    '/163189/gdzj':     ('广东珠江',    f'{BASE}/广东珠江.png'),
    '/163189/gdty':     ('广东体育',    f'{BASE}/广东体育.png'),
    '/163189/lhdy':     ('龙华电影',    f'{BASE}/龙华电影.png'),
    '/163189/lhjd':     ('龙华经典',    f'{BASE}/龙华经典.png'),
    '/163189/lhox':     ('龙华偶像',    f'{BASE}/龙华偶像.png'),
    '/163189/lhrh':     ('龙华日韩',    f'{BASE}/龙华日韩.png'),
    '/163189/lhxj':     ('龙华戏剧',    f'{BASE}/龙华戏剧.png'),
    '/163189/lhyp':     ('龙华洋片',    f'{BASE}/龙华洋片.png'),
    '/163189/lhkt':     ('龙华卡通',    f'{BASE}/龙华卡通.png'),
    '/163189/jgbdx':    ('金光布袋戏',  f'{BASE}/金光布袋戏.png'),
    '/163189/xinghe':   ('TVB星河',     f'{BASE}/TVB 星河.png'),
    '/163189/typd':     ('天映频道',    f'{BASE}/天映频道.png'),
    '/163189/ccm':      ('天映经典',    f'{BASE}/天映经典.png'),
    '/163189/hxws2':    ('海峡卫视',    f'{BASE}/海峡卫视.png'),
    '/163189/cctv13-2': ('CCTV13',     f'{BASE}/CCTV13.png'),
    '/163189/wxty':     ('五星体育',    f'{BASE}/五星体育.png'),
}


def build_url_map(ref_m3u_path: str) -> dict:
    """从参考 M3U 文件提取 URL路径后缀 → (tvg-name, tvg-logo) 映射。"""
    content = open(ref_m3u_path, encoding='utf-8').read()
    pattern = re.compile(r'#EXTINF:-1([^\n]*)\n(https?://[^\n]+)', re.MULTILINE)
    url_map = {}
    for m in pattern.finditer(content):
        attrs = m.group(1)
        url   = m.group(2).strip()
        path  = re.sub(r'^https?://[^/]+', '', url)   # 去掉 scheme+host
        name_m = re.search(r'tvg-name="([^"]*)"', attrs)
        logo_m = re.search(r'tvg-logo="([^"]*)"', attrs)
        tvg_name = name_m.group(1) if name_m else ''
        tvg_logo = logo_m.group(1) if logo_m else ''
        if (tvg_name or tvg_logo) and path not in url_map:
            url_map[path] = (tvg_name, tvg_logo)
    return url_map


def convert(input_path: str, ref_m3u_path: str, output_path: str) -> None:
    """主转换函数：读取输入文件，输出带 tvg-name/tvg-logo 的 M3U。"""
    # 1. 构建映射表（参考文件优先，再用内置补充表填充）
    url_map = build_url_map(ref_m3u_path)
    # 内置补充表中只添加参考文件没有的条目
    for path, info in EXTRA_MAP.items():
        if path not in url_map:
            url_map[path] = info

    # 2. 逐行处理输入文件
    raw_lines = open(input_path, encoding='utf-8').read().splitlines()
    output = ['#EXTM3U']
    matched = 0
    unmatched_channels = []

    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split(',', 1)
        if len(parts) != 2:
            continue
        ch_name, url = parts
        path = re.sub(r'^https?://[^/]+', '', url)

        if path in url_map:
            tvg_name, tvg_logo = url_map[path]
            output.append(f'#EXTINF:-1 tvg-name="{tvg_name}" tvg-logo="{tvg_logo}",{ch_name}')
            matched += 1
        else:
            output.append(f'#EXTINF:-1,{ch_name}')
            unmatched_channels.append(f'{ch_name} -> {path}')
        output.append(url)

    # 3. 写入输出文件
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(output) + '\n')

    # 4. 统计报告
    total = len([l for l in raw_lines if l.strip()])
    print(f'✅ 完成！共 {total} 条，匹配: {matched}，未匹配: {len(unmatched_channels)}')
    print(f'📄 输出文件: {output_path}')
    if unmatched_channels:
        print('⚠️  以下频道无 tvg 信息（仅使用频道名）:')
        for ch in unmatched_channels:
            print(f'   {ch}')


if __name__ == '__main__':
    convert(INPUT_FILE, REF_M3U, OUTPUT_FILE)
