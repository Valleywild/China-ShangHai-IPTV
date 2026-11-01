#!/usr/bin/env python3
"""
merge_m3u_aptv.py
合并 央视频直播.m3u 与 IPTV_Merged_change.m3u，
使其完全符合 APTV 合并相同频道的聚合条件：
1. 相同频道名称完全对齐与规范化（消除 4K/HD 干扰，统一央视、卫视命名）
2. 同一频道的多条播放线路相邻连续放置，自动合并为主备多线路
3. 彻底删除 group-title 属性，避免被 APTV 分组切分导致无法聚合
4. 补充和补齐完整 tvg-id、tvg-name、tvg-logo 及回看 (catchup) 元数据
"""

import os
import re
import urllib.parse
from collections import OrderedDict

# ── 1. 解析 M3U 文件 ──────────────────────────────────────────────────────────
def parse_m3u(file_path):
    channels = []
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
    current_extinf = None
    for line in lines:
        line = line.strip()
        if line.startswith('#EXTINF:'):
            current_extinf = line
        elif line and not line.startswith('#'):
            if current_extinf:
                comma_idx = current_extinf.rfind(',')
                title = current_extinf[comma_idx + 1:].strip() if comma_idx != -1 else ''
                attrs = current_extinf[:comma_idx] if comma_idx != -1 else current_extinf
                channels.append({
                    'extinf': current_extinf,
                    'attrs': attrs,
                    'title': title,
                    'url': line
                })
                current_extinf = None
    return channels

# ── 2. 频道名称规范化对齐映射 ──────────────────────────────────────────────────
cctv_map = {
    'CCTV-1HD': 'CCTV-1 综合', 'CCTV-2HD': 'CCTV-2 财经', 'CCTV-3HD': 'CCTV-3 综艺',
    'CCTV-4HD': 'CCTV-4 中文国际', 'CCTV-5HD': 'CCTV-5 体育', 'CCTV5': 'CCTV-5 体育',
    'CCTV5+': 'CCTV-5+ 体育赛事', 'CCTV-6HD': 'CCTV-6 电影', 'CCTV-7HD': 'CCTV-7 国防军事',
    'CCTV-8HD': 'CCTV-8 电视剧', 'CCTV-9HD': 'CCTV-9 纪录', 'CCTV-10HD': 'CCTV-10 科教',
    'CCTV-11HD': 'CCTV-11 戏曲', 'CCTV-12HD': 'CCTV-12 社会与法', 'CCTV-13HD': 'CCTV-13 新闻',
    'CCTV13': 'CCTV-13 新闻', 'CCTV-14HD': 'CCTV-14 少儿', 'CCTV-15HD': 'CCTV-15 音乐',
    'CCTV16': 'CCTV-16 奥林匹克', 'CCTV-17HD': 'CCTV-17 农业农村', 'CCTV-4K': 'CCTV-4K 超高清',
    '风云剧场HD': 'CCTV 风云剧场', '第一剧场HD': 'CCTV 第一剧场', '怀旧剧场HD': 'CCTV 怀旧剧场',
    '中国教育-1HD': 'CETV-1',
}

ws_4k_hd = [
    '东方卫视', '北京卫视', '江苏卫视', '浙江卫视', '湖南卫视', '四川卫视', '山东卫视',
    '广东卫视', '深圳卫视', '黑龙江卫视', '安徽卫视', '天津卫视', '江西卫视', '辽宁卫视',
    '湖北卫视', '东南卫视', '重庆卫视', '河北卫视', '内蒙古卫视', '河南卫视', '山西卫视',
    '陕西卫视', '甘肃卫视', '青海卫视', '云南卫视', '广西卫视', '贵州卫视', '宁夏卫视',
    '新疆卫视', '西藏卫视', '吉林卫视', '海南卫视', '兵团卫视', '三沙卫视', '延边卫视'
]

special_map = {
    '五星体育HD': '五星体育',
    '金鹰卡通HD': '金鹰卡通',
    '金鹰纪实HD': '金鹰纪实',
    '快乐垂钓HD': '快乐垂钓',
    '上海教育HD': '上海教育',
    '上海教育电视台': '上海教育',
}

def normalize_title(title):
    if title in cctv_map:
        return cctv_map[title]
    for ws in ws_4k_hd:
        if title.startswith(ws) and (title.endswith('4K') or title.endswith('HD')):
            return ws
    if title in special_map:
        return special_map[title]
    if title.endswith('HD'):
        return title[:-2].strip()
    return title

# ── 3. 主合并流程 ──────────────────────────────────────────────────────────────
def merge(file_ysp, file_iptv, output_file):
    c1 = parse_m3u(file_ysp)
    c2 = parse_m3u(file_iptv)

    # 提取 c1 (央视频) 的标准元数据表
    c1_meta = {}
    for item in c1:
        title = item['title']
        extinf = item['extinf']
        tvg_id_m = re.search(r'tvg-id=\"([^\"]+)\"', extinf)
        tvg_name_m = re.search(r'tvg-name=\"([^\"]+)\"', extinf)
        tvg_logo_m = re.search(r'tvg-logo=\"([^\"]+)\"', extinf)
        catchup_m = re.search(r'catchup=\"([^\"]+)\"', extinf)
        catchup_source_m = re.search(r'catchup-source=\"([^\"]+)\"', extinf)
        c1_meta[title] = {
            'tvg-id': tvg_id_m.group(1) if tvg_id_m else '',
            'tvg-name': tvg_name_m.group(1) if tvg_name_m else title,
            'tvg-logo': tvg_logo_m.group(1) if tvg_logo_m else '',
            'catchup': catchup_m.group(1) if catchup_m else 'append',
            'catchup-source': catchup_source_m.group(1) if catchup_source_m else '?playseek=${(b)yyyyMMddHHmmss}-${(e)yyyyMMddHHmmss}',
        }

    # 加载参考 logo 库 (来自 IPTV_Merged_logos.m3u 及本地 tv_icons)
    ref_logos = {}
    if os.path.exists('IPTV_Merged_logos.m3u'):
        with open('IPTV_Merged_logos.m3u', 'r', encoding='utf-8') as f:
            for m in re.finditer(r'#EXTINF:[^\n]*tvg-logo=\"([^\"]+)\"[^\n]*,([^\n]+)', f.read()):
                logo_url = m.group(1).strip()
                ch_name = m.group(2).strip()
                clean = ch_name.replace('HD', '').replace('4K', '').strip()
                ref_logos[clean] = logo_url
                ref_logos[ch_name] = logo_url

    tv_icons = set(os.listdir('tv_icons')) if os.path.exists('tv_icons') else set()
    base_icon_url = 'https://fastly.jsdelivr.net/gh/Valleywild/China-ShangHai-IPTV@master/tv_icons/'

    # 按照 IPTV 原源顺序构建频道主骨架
    ordered_groups = OrderedDict()
    for item in c2:
        nt = normalize_title(item['title'])
        if nt not in ordered_groups:
            ordered_groups[nt] = []
        # 去除同一频道下完全相同的流地址
        if item['url'] not in [x['url'] for x in ordered_groups[nt]]:
            ordered_groups[nt].append(item)

    # 将央视频中匹配的频道追加到对应频道后（作为备选线路）
    c1_remaining = OrderedDict()
    for item in c1:
        nt = normalize_title(item['title'])
        if nt in ordered_groups:
            if item['url'] not in [x['url'] for x in ordered_groups[nt]]:
                ordered_groups[nt].append(item)
        else:
            if nt not in c1_remaining:
                c1_remaining[nt] = []
            c1_remaining[nt].append(item)

    # 规范化插入央视频独有的 8 个频道至最恰当顺位
    final_groups = OrderedDict()
    for ch_name, streams in ordered_groups.items():
        final_groups[ch_name] = streams
        if ch_name == 'CCTV-16 奥林匹克' and 'CCTV-16 4K' in c1_remaining:
            final_groups['CCTV-16 4K'] = c1_remaining.pop('CCTV-16 4K')
        elif ch_name == 'CCTV-4K 超高清' and 'CCTV-8K 超高清' in c1_remaining:
            final_groups['CCTV-8K 超高清'] = c1_remaining.pop('CCTV-8K 超高清')
        elif ch_name == 'CGTN':
            for cgtn_lang in ['CGTN 纪录', 'CGTN 法语', 'CGTN 俄语', 'CGTN 阿拉伯语', 'CGTN 西班牙语']:
                if cgtn_lang in c1_remaining:
                    final_groups[cgtn_lang] = c1_remaining.pop(cgtn_lang)
        elif ch_name == 'CETV-1' and '国学频道' in c1_remaining:
            final_groups['国学频道'] = c1_remaining.pop('国学频道')

    # 剩余若有追加至末尾
    for ch_name, streams in c1_remaining.items():
        final_groups[ch_name] = streams

    # 确定每个频道的代表性 logo 和 tvg-id
    def get_channel_canonical_meta(ch_name, streams):
        tvg_id = ''
        if ch_name in c1_meta and c1_meta[ch_name]['tvg-id']:
            tvg_id = c1_meta[ch_name]['tvg-id']
        else:
            for s in streams:
                m = re.search(r'tvg-id=\"([^\"]+)\"', s['attrs'])
                if m:
                    tvg_id = m.group(1)
                    break

        tvg_logo = ''
        for s in streams:
            m = re.search(r'tvg-logo=\"([^\"]+)\"', s['attrs'])
            if m:
                tvg_logo = m.group(1)
                break
        if not tvg_logo:
            if ch_name in c1_meta and c1_meta[ch_name]['tvg-logo']:
                tvg_logo = c1_meta[ch_name]['tvg-logo']
            elif ch_name in ref_logos:
                tvg_logo = ref_logos[ch_name]
            elif f'{ch_name}.png' in tv_icons:
                tvg_logo = f'{base_icon_url}{urllib.parse.quote(ch_name + ".png")}'

        return tvg_id, tvg_logo

    # ── 4. 格式化单行 EXTINF ────────────────────────────────────────────────────
    def format_extinf_line(ch_name, stream, canonical_id, canonical_logo):
        attrs = stream['attrs']
        
        # 严格删除 group-title
        attrs_no_group = re.sub(r'\s*group-title=\"[^\"]*\"', '', attrs)
        attrs_no_group = re.sub(r'\s*group-title=[^\s,]+', '', attrs_no_group)

        # tvg-id
        tvg_id = ''
        m = re.search(r'tvg-id=\"([^\"]+)\"', attrs_no_group)
        if m:
            tvg_id = m.group(1)
        elif canonical_id:
            tvg_id = canonical_id

        # tvg-name 保证完全对齐
        tvg_name = ch_name

        # tvg-logo
        tvg_logo = ''
        m = re.search(r'tvg-logo=\"([^\"]+)\"', attrs_no_group)
        if m:
            tvg_logo = m.group(1)
        elif canonical_logo:
            tvg_logo = canonical_logo

        # catchup
        catchup = ''
        m = re.search(r'catchup=\"([^\"]+)\"', attrs_no_group)
        if m:
            catchup = m.group(1)
        elif ch_name in c1_meta and c1_meta[ch_name]['catchup']:
            catchup = c1_meta[ch_name]['catchup']

        catchup_source = ''
        m = re.search(r'catchup-source=\"([^\"]+)\"', attrs_no_group)
        if m:
            catchup_source = m.group(1)
        elif ch_name in c1_meta and c1_meta[ch_name]['catchup-source']:
            catchup_source = c1_meta[ch_name]['catchup-source']

        parts = ['#EXTINF:-1']
        if tvg_id:
            parts.append(f'tvg-id="{tvg_id}"')
        if tvg_name:
            parts.append(f'tvg-name="{tvg_name}"')
        if tvg_logo:
            parts.append(f'tvg-logo="{tvg_logo}"')
        if catchup:
            parts.append(f'catchup="{catchup}"')
        if catchup_source:
            parts.append(f'catchup-source="{catchup_source}"')

        return ' '.join(parts) + f',{ch_name}'

    # ── 5. 输出组装 ────────────────────────────────────────────────────────────
    output_lines = [
        '#EXTM3U x-tvg-url="https://live.fanmingming.cn/e.xml,https://epg.pw/xmltv/epg_CN.xml.gz,https://epg.zsdc.eu.org/t.xml.gz" catchup="append" catchup-source="?playseek=${(b)yyyyMMddHHmmss}-${(e)yyyyMMddHHmmss}"'
    ]

    for ch_name, streams in final_groups.items():
        canonical_id, canonical_logo = get_channel_canonical_meta(ch_name, streams)
        for s in streams:
            extinf_str = format_extinf_line(ch_name, s, canonical_id, canonical_logo)
            output_lines.append(extinf_str)
            output_lines.append(s['url'])

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(output_lines) + '\n')

    print(f'✅ 合并成功！输出文件: {output_file}')
    print(f'📊 统计:')
    print(f'   - 独立电视频道数: {len(final_groups)}')
    print(f'   - 累计总直播流数: {sum(len(s) for s in final_groups.values())}')
    merged_channels = [k for k, v in final_groups.items() if len(v) > 1]
    print(f'   - 成功聚合多线路频道数: {len(merged_channels)}')

if __name__ == '__main__':
    merge('央视频直播.m3u', 'IPTV_Merged_change.m3u', 'IPTV_Merged_APTV.m3u')
