#!/usr/bin/env python3
"""
conver_to_m3u_class.py
将 "频道名,URL" 格式的 txt 文件转换为标准 M3U 格式（class 重构版）。
自动从参考 M3U 文件提取 tvg-name / tvg-logo，未匹配频道使用内置补充映射表。

用法:
    python3 conver_to_m3u_class.py [输入文件] [参考M3U] [输出文件]
默认:
    输入文件  = temp1.txt
    参考M3U   = IPTV_Merged_change.m3u
    输出文件  = temp1_converted.m3u
"""

import re
import sys
from dataclasses import dataclass, field
from typing import Optional


# ── Logo 库前缀常量 ──────────────────────────────────────────────────────────
_BASE  = "https://gcore.jsdelivr.net/gh/luoluowoaini/666-@iill-logo-mytvsuper"
_BASE2 = "https://gcore.jsdelivr.net/gh/luoluowoaini/666-@main"
_TAKS  = "https://gcore.jsdelivr.net/gh/taksssss/tv@main/icon"

# ── 内置补充映射（参考 M3U 中没有对应条目的频道）────────────────────────────
_DEFAULT_EXTRA_MAP: dict[str, tuple[str, str]] = {
    '/163189/hoy2':     ('HOY77',      f'{_BASE}/HOY TV.png'),
    '/163189/hoy78':    ('HOY78',      f'{_BASE}/HOY TV.png'),
    '/163189/hoy76':    ('HOY76',      f'{_BASE}/HOY TV.png'),
    '/163189/viu':      ('ViuTV',      f'{_BASE}/ViuTV.png'),
    '/163189/viu6':     ('ViuTVsix',   f'{_BASE}/Viu TV Six.png'),
    '/163189/now':      ('NOW新闻台',   f'{_BASE}/Now News.png'),
    '/163189/rthk31':   ('RTHK31',     f'{_BASE}/RTHK31.png'),
    '/163189/rthk32':   ('RTHK32',     f'{_BASE}/RTHK32.png'),
    '/163189/gdzj':     ('广东珠江',    f'{_BASE}/广东珠江.png'),
    '/163189/gdty':     ('广东体育',    f'{_BASE}/广东体育.png'),
    '/163189/lhdy':     ('龙华电影',    f'{_BASE}/龙华电影.png'),
    '/163189/lhjd':     ('龙华经典',    f'{_BASE}/龙华经典.png'),
    '/163189/lhox':     ('龙华偶像',    f'{_BASE}/龙华偶像.png'),
    '/163189/lhrh':     ('龙华日韩',    f'{_BASE}/龙华日韩.png'),
    '/163189/lhxj':     ('龙华戏剧',    f'{_BASE}/龙华戏剧.png'),
    '/163189/lhyp':     ('龙华洋片',    f'{_BASE}/龙华洋片.png'),
    '/163189/lhkt':     ('龙华卡通',    f'{_BASE}/龙华卡通.png'),
    '/163189/jgbdx':    ('金光布袋戏',  f'{_BASE}/金光布袋戏.png'),
    '/163189/xinghe':   ('TVB星河',     f'{_BASE}/TVB 星河.png'),
    '/163189/typd':     ('天映频道',    f'{_BASE}/天映频道.png'),
    '/163189/ccm':      ('天映经典',    f'{_BASE}/天映经典.png'),
    '/163189/hxws2':    ('海峡卫视',    f'{_BASE}/海峡卫视.png'),
    '/163189/cctv13-2': ('CCTV13',     f'{_BASE}/CCTV13.png'),
    '/163189/wxty':     ('五星体育',    f'{_BASE}/五星体育.png'),
}


@dataclass
class ConversionResult:
    """记录单次转换的统计结果。"""
    total: int = 0
    matched: int = 0
    unmatched_channels: list[str] = field(default_factory=list)

    @property
    def unmatched(self) -> int:
        return len(self.unmatched_channels)

    def report(self) -> None:
        """打印转换统计报告。"""
        print(f'✅ 完成！共 {self.total} 条，匹配: {self.matched}，未匹配: {self.unmatched}')
        if self.unmatched_channels:
            print('⚠️  以下频道无 tvg 信息（仅使用频道名）:')
            for ch in self.unmatched_channels:
                print(f'   {ch}')


class M3UConverter:
    """
    将 "频道名,URL" 格式的纯文本转换为标准 M3U 格式。

    Attributes:
        input_path:   输入文件路径（"频道名,URL" 格式）
        ref_m3u_path: 参考 M3U 文件路径，用于提取 tvg-name / tvg-logo
        output_path:  输出 M3U 文件路径
        extra_map:    内置补充映射表（参考文件未覆盖的频道）
    """

    _EXTINF_PATTERN = re.compile(
        r'#EXTINF:-1([^\n]*)\n(https?://[^\n]+)', re.MULTILINE
    )
    _HOST_PATTERN = re.compile(r'^https?://[^/]+')

    def __init__(
        self,
        input_path: str,
        ref_m3u_path: str,
        output_path: str,
        extra_map: Optional[dict[str, tuple[str, str]]] = None,
    ) -> None:
        self.input_path   = input_path
        self.ref_m3u_path = ref_m3u_path
        self.output_path  = output_path
        self.extra_map    = extra_map if extra_map is not None else _DEFAULT_EXTRA_MAP

        # 内部状态
        self._url_map: dict[str, tuple[str, str]] = {}

    # ── 私有辅助方法 ─────────────────────────────────────────────────────────

    @staticmethod
    def _url_to_path(url: str) -> str:
        """从完整 URL 提取路径部分（去掉 scheme + host）。"""
        return M3UConverter._HOST_PATTERN.sub('', url)

    def _build_url_map(self) -> None:
        """
        从参考 M3U 文件提取 URL路径 → (tvg-name, tvg-logo) 映射，
        再用 extra_map 填充参考文件未覆盖的条目。
        """
        content = open(self.ref_m3u_path, encoding='utf-8').read()
        url_map: dict[str, tuple[str, str]] = {}

        for m in self._EXTINF_PATTERN.finditer(content):
            attrs = m.group(1)
            url   = m.group(2).strip()
            path  = self._url_to_path(url)

            name_m = re.search(r'tvg-name="([^"]*)"', attrs)
            logo_m = re.search(r'tvg-logo="([^"]*)"', attrs)
            tvg_name = name_m.group(1) if name_m else ''
            tvg_logo = logo_m.group(1) if logo_m else ''

            if (tvg_name or tvg_logo) and path not in url_map:
                url_map[path] = (tvg_name, tvg_logo)

        # 用内置补充表填充参考文件未收录的频道（参考文件优先）
        for path, info in self.extra_map.items():
            if path not in url_map:
                url_map[path] = info

        self._url_map = url_map

    def _build_extinf_line(self, ch_name: str, url: str) -> tuple[str, bool]:
        """
        构建 #EXTINF 行。
        返回 (extinf_line, matched) ，matched 表示是否找到 tvg 信息。
        """
        path = self._url_to_path(url)
        if path in self._url_map:
            tvg_name, tvg_logo = self._url_map[path]
            line = f'#EXTINF:-1 tvg-name="{tvg_name}" tvg-logo="{tvg_logo}",{ch_name}'
            return line, True
        return f'#EXTINF:-1,{ch_name}', False

    def _parse_input(self) -> list[tuple[str, str]]:
        """
        解析输入文件，返回 [(ch_name, url), ...] 列表，跳过空行和格式错误行。
        """
        channels = []
        for line in open(self.input_path, encoding='utf-8').read().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(',', 1)
            if len(parts) == 2:
                channels.append((parts[0], parts[1]))
        return channels

    # ── 公开方法 ─────────────────────────────────────────────────────────────

    def run(self) -> ConversionResult:
        """
        执行完整转换流程：
        1. 构建 tvg 映射表
        2. 解析输入频道列表
        3. 生成 M3U 内容并写入输出文件
        4. 返回转换统计结果

        Returns:
            ConversionResult: 包含 total / matched / unmatched_channels 的统计对象
        """
        self._build_url_map()

        channels = self._parse_input()
        result = ConversionResult(total=len(channels))

        output_lines = ['#EXTM3U']
        for ch_name, url in channels:
            extinf_line, matched = self._build_extinf_line(ch_name, url)
            output_lines.append(extinf_line)
            output_lines.append(url)
            if matched:
                result.matched += 1
            else:
                result.unmatched_channels.append(
                    f'{ch_name} -> {self._url_to_path(url)}'
                )

        with open(self.output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(output_lines) + '\n')

        print(f'📄 输出文件: {self.output_path}')
        return result


# ── 入口 ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    input_file  = sys.argv[1] if len(sys.argv) > 1 else 'temp1.txt'
    ref_m3u     = sys.argv[2] if len(sys.argv) > 2 else 'IPTV_Merged_change.m3u'
    output_file = sys.argv[3] if len(sys.argv) > 3 else 'temp1_converted.m3u'

    converter = M3UConverter(input_file, ref_m3u, output_file)
    result = converter.run()
    result.report()
