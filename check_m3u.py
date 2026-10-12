#!/usr/bin/env python3
"""
IPTV M3U 本地连接性快速测试与筛选工具 (无代理直连检测)
功能:
  1. 支持远程 M3U URL 或本地 M3U 文件路径输入 (支持命令行或交互式输入)
  2. 强制禁用代理 (Direct Connect)，确保只保留本地可直连播放的频道
  3. 支持移除/排除指定分组 (例如: 虎牙, 抖音)，支持白名单包含指定分组
  4. 支持 --list-groups 快速查看源内包含的所有分组及各自分组的频道数
  5. 多线程高并发检测，秒级超时，极速筛选
  6. 智能识别 HLS (m3u8)、HTTP-TS、FLV、RTSP 以及内网组播 (RTP/UDP)
  7. 自动保留全部 M3U 元数据 (tvg-logo, group-title, tvg-name 等)
  8. 输出筛选后的可用 M3U 文件，支持按响应延迟测速排序
"""

import sys
import os
import re
import time
import socket
import struct
import argparse
import ssl
from urllib.parse import urlsplit, urlunsplit, quote, urljoin
import urllib.request
import urllib.error
import http.client
import locale
import gzip
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    locale.setlocale(locale.LC_COLLATE, 'zh_CN.UTF-8')
except Exception:
    pass


def natural_sort_key(s: str):
    """自然排序键值函数：数字按数值大小排序(如 CCTV2 < CCTV10)，中文按拼音排序"""
    parts = re.split(r'(\d+)', s)
    key = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        elif part:
            try:
                key.append((1, locale.strxfrm(part.lower())))
            except Exception:
                key.append((1, part.lower()))
    return key

# 频道测速专用：强制无代理直连 (基于 ProxyHandler({}))
# 注意：保留环境变量中的代理仅供获取境外 M3U 索引文件时兜底使用，所有频道测速均通过 direct_opener 强制直连


# 默认请求头 (模拟常用播放器/浏览器，避免被服务端 UA 拦截)
DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Connection': 'close',
}

# 全局忽略 SSL 证书错误 (避免内网或自签名证书源被误判)
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

# 强制无代理的 urllib opener
no_proxy_handler = urllib.request.ProxyHandler({})
https_handler = urllib.request.HTTPSHandler(context=ssl_ctx)
direct_opener = urllib.request.build_opener(no_proxy_handler, https_handler)


def safe_url(url: str) -> str:
    """转义 URL 中可能存在的非 ASCII 字符或空格，避免 urllib 解析崩溃"""
    try:
        parts = urlsplit(url.strip())
        path = quote(parts.path, safe='/%')
        query = quote(parts.query, safe='=&?/%+:')
        return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))
    except Exception:
        return url.strip()


class ChannelItem:
    def __init__(self, raw_lines, name, url, group=""):
        self.raw_lines = raw_lines  # 原始标签行列表 (保留 #EXTINF, #EXTGRP 等)
        self.name = name
        self.url = url
        self.group = group
        self.is_valid = False
        self.latency_ms = 99999
        self.error_msg = ""


def parse_m3u(content: str):
    """解析 M3U 内容，提取头部信息和每个频道的完整定义"""
    lines = content.splitlines()
    header_lines = []
    channels = []
    
    current_tags = []
    current_name = "未知频道"
    current_group = ""
    in_header = True

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith('#EXTM3U'):
            header_lines.append(stripped)
            in_header = False
            continue

        if stripped.startswith('#'):
            if in_header:
                header_lines.append(stripped)
            else:
                current_tags.append(stripped)
                if stripped.startswith('#EXTINF:'):
                    parts = stripped.split(',', 1)
                    if len(parts) > 1:
                        current_name = parts[1].strip()
                    grp_match = (
                        re.search(r'group-title="([^"]*)"', stripped) or
                        re.search(r"group-title='([^']*)'", stripped) or
                        re.search(r'group-title=([^ ,"\n\r]+)', stripped)
                    )
                    if grp_match:
                        current_group = grp_match.group(1).strip()
                elif stripped.startswith('#EXTGRP:'):
                    current_group = stripped.split(':', 1)[1].strip()
        else:
            in_header = False
            url = stripped
            item = ChannelItem(
                raw_lines=list(current_tags),
                name=current_name,
                url=url,
                group=current_group
            )
            channels.append(item)
            current_tags = []
            current_name = "未知频道"
            current_group = ""

    if not header_lines:
        header_lines.append('#EXTM3U')

    return header_lines, channels


def parse_patterns(arg_list):
    """规范化处理命令行传入的分组列表 (支持多参数、逗号分隔)"""
    if not arg_list:
        return []
    patterns = []
    for item in arg_list:
        for p in item.split(','):
            p = p.strip()
            if p:
                patterns.append(p)
    return patterns


def is_group_matched(group_name: str, patterns: list, exact: bool = False) -> bool:
    """检查 group_name 是否匹配 patterns 中的任意模式"""
    if not group_name or not patterns:
        return False
    g_target = group_name.strip()
    if exact:
        return any(p.strip().lower() == g_target.lower() for p in patterns)
    else:
        return any(p.strip().lower() in g_target.lower() for p in patterns)


def test_udp_multicast(url: str, timeout: float = 2.0):
    """测试 RTP / UDP 组播流"""
    start_time = time.time()
    try:
        clean_url = url.replace('rtp://@', 'rtp://').replace('udp://@', 'udp://')
        parsed = urlsplit(clean_url)
        host = parsed.hostname or parsed.netloc.split(':')[0]
        port = parsed.port or 5140
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(timeout)
        
        sock.bind(('', port))
        mreq = struct.pack("4sl", socket.inet_aton(host), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        
        data, _ = sock.recvfrom(2048)
        sock.close()
        latency = int((time.time() - start_time) * 1000)
        return (len(data) > 0, "OK" if len(data) > 0 else "无数据", latency)
    except Exception as e:
        return (False, str(e), 0)


def test_rtsp_stream(url: str, timeout: float = 2.5):
    """测试 RTSP 流连通性"""
    start_time = time.time()
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port or 554
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            req = f"OPTIONS {url} RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: Lavf/58.76.100\r\n\r\n"
            s.sendall(req.encode('utf-8'))
            resp = s.recv(1024)
            if b"RTSP/1.0" in resp or b"200 OK" in resp or len(resp) > 0:
                latency = int((time.time() - start_time) * 1000)
                return (True, "OK", latency)
        return (False, "无响应", 0)
    except Exception as e:
        return (False, str(e), 0)


def test_http_stream(url: str, timeout: float = 3.0, check_segment: bool = False):
    """测试 HTTP/HTTPS 流 (HLS 播放列表或 Direct Video Stream)"""
    start_time = time.time()
    sanitized_url = safe_url(url)
    try:
        req = urllib.request.Request(sanitized_url, headers=DEFAULT_HEADERS, method='GET')
        with direct_opener.open(req, timeout=timeout) as resp:
            status = resp.status
            if status not in (200, 206):
                return (False, f"HTTP {status}", 0)
            
            chunk = resp.read(8192)
            if not chunk:
                return (False, "空响应", 0)

            content_text = ""
            try:
                content_text = chunk.decode('utf-8', errors='ignore')
            except Exception:
                pass

            # 判定是否为 HLS M3U8 播放列表
            if '#EXTM3U' in content_text:
                if '#EXT-X-STREAM-INF' in content_text:
                    # Master Playlist: 查找子流地址
                    sub_urls = [l.strip() for l in content_text.splitlines() if l.strip() and not l.startswith('#')]
                    if sub_urls and check_segment:
                        sub_url = urljoin(sanitized_url, sub_urls[0])
                        return test_http_stream(sub_url, timeout=timeout, check_segment=check_segment)
                elif '#EXTINF' in content_text:
                    # Media Playlist: 可选深入测试首个切片连通性
                    if check_segment:
                        segments = [l.strip() for l in content_text.splitlines() if l.strip() and not l.startswith('#')]
                        if segments:
                            seg_url = urljoin(sanitized_url, segments[0])
                            seg_req = urllib.request.Request(seg_url, headers=DEFAULT_HEADERS, method='GET')
                            with direct_opener.open(seg_req, timeout=timeout) as seg_resp:
                                seg_chunk = seg_resp.read(2048)
                                if not seg_chunk:
                                    return (False, "切片下载失败", 0)
                latency = int((time.time() - start_time) * 1000)
                return (True, "OK", latency)
            
            # 非 m3u8，直接媒体流 (TS/MP4/FLV 等)
            # 排除返回 200 但实际是 HTML 错误拦截页或提示页的情况
            content_type = (resp.headers.get('Content-Type') or '').lower()
            if 'text/html' in content_type and ('<html' in content_text.lower() or '<!doctype' in content_text.lower()):
                return (False, "非视频流(返回网页内容)", 0)

            latency = int((time.time() - start_time) * 1000)
            return (True, "OK", latency)

    except urllib.error.HTTPError as e:
        return (False, f"HTTP {e.code}", 0)
    except urllib.error.URLError as e:
        reason = str(e.reason)
        if "timed out" in reason.lower():
            return (False, "超时", 0)
        return (False, reason, 0)
    except socket.timeout:
        return (False, "超时", 0)
    except Exception as e:
        return (False, str(e), 0)


def check_channel(channel: ChannelItem, timeout: float = 3.0, check_segment: bool = False):
    """检测单个频道的本地直连可用性"""
    url = channel.url
    if url.startswith('rtp://') or url.startswith('udp://'):
        ok, msg, latency = test_udp_multicast(url, timeout=timeout)
    elif url.startswith('rtsp://'):
        ok, msg, latency = test_rtsp_stream(url, timeout=timeout)
    elif url.startswith('http://') or url.startswith('https://'):
        ok, msg, latency = test_http_stream(url, timeout=timeout, check_segment=check_segment)
    else:
        proto = url.split('://')[0] if '://' in url else url
        ok, msg, latency = (False, f"未知协议({proto})", 0)

    channel.is_valid = ok
    channel.error_msg = msg
    channel.latency_ms = latency if ok else 99999
    return channel


class RedirectTracker(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        super().__init__()
        self.chain = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.chain.append((code, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def diagnose_single_url(url: str, timeout: float = 5.0):
    """单频道/单链接深度诊断模式 (打印握手、重定向追踪、视频格式及延迟)"""
    print(f"\n🔍 正在诊断单频道本地连接性 (严格强制无代理直连)...")
    print(f"📡 目标地址: {url}")
    print("-" * 68)

    if url.startswith('rtp://') or url.startswith('udp://'):
        ok, msg, latency = test_udp_multicast(url, timeout=timeout)
        print(f"  • 协议类型:   UDP / RTP 组播流")
        print(f"  • 连通结果:   {'✅ 成功接收到组播数据包' if ok else '❌ 连接失败'}")
        print(f"  • 详细信息:   {msg}")
        if ok:
            print(f"  • 首包延迟:   {latency} ms")
            print(f"  • 最终判定:   ✅ 本地可直接播放 (无代理)")
        else:
            print(f"  • 最终判定:   ❌ 本地无法直连")
        print("-" * 68)
        return

    if url.startswith('rtsp://'):
        ok, msg, latency = test_rtsp_stream(url, timeout=timeout)
        print(f"  • 协议类型:   RTSP 流媒体协议")
        print(f"  • 连通结果:   {'✅ RTSP 握手成功' if ok else '❌ 握手失败'}")
        print(f"  • 详细信息:   {msg}")
        if ok:
            print(f"  • 握手延迟:   {latency} ms")
            print(f"  • 最终判定:   ✅ 本地可直接播放 (无代理)")
        else:
            print(f"  • 最终判定:   ❌ 本地无法直连")
        print("-" * 68)
        return

    # HTTP / HTTPS
    tracker = RedirectTracker()
    opener = urllib.request.build_opener(
        no_proxy_handler,
        https_handler,
        tracker
    )
    start_time = time.time()
    sanitized_url = safe_url(url)
    try:
        req = urllib.request.Request(sanitized_url, headers=DEFAULT_HEADERS, method='GET')
        with opener.open(req, timeout=timeout) as resp:
            latency = int((time.time() - start_time) * 1000)
            status = resp.status
            final_url = resp.geturl()
            content_type = resp.headers.get('Content-Type') or '未知'
            server = resp.headers.get('Server') or '未知'

            print(f"  • 初始连接:   HTTP {status} OK")
            if tracker.chain:
                print(f"  • 302 重定向: 经历了 {len(tracker.chain)} 次跳转")
                for code, target in tracker.chain:
                    print(f"      ↪ [{code}] {target}")
                print(f"  • 最终源地址: {final_url}")
            
            print(f"  • 服务端引擎: {server}")
            print(f"  • 内容类型:   {content_type}")
            print(f"  • 完整耗时:   {latency} ms (包含握手与重定向)")

            chunk = resp.read(8192)
            if not chunk:
                print(f"  • 媒体检测:   ❌ 服务端返回空内容")
                print(f"  • 最终判定:   ❌ 不可用 (返回空流)")
                print("-" * 68)
                return

            content_text = ""
            try:
                content_text = chunk.decode('utf-8', errors='ignore')
            except Exception:
                pass

            if '#EXTM3U' in content_text:
                if '#EXT-X-STREAM-INF' in content_text:
                    stream_type = "HLS 自适应多码率主索引 (Master Playlist)"
                elif '#EXTINF' in content_text:
                    stream_type = "HLS 媒体切片播放列表 (Media Playlist)"
                else:
                    stream_type = "HLS m3u8 播放列表"
                print(f"  • 流媒体格式: {stream_type}")
                print(f"  • 最终判定:   ✅ 本地可无代理秒播 (Direct Playable)")
            elif 'text/html' in content_type.lower() and ('<html' in content_text.lower() or '<!doctype' in content_text.lower()):
                print(f"  • 流媒体格式: ⚠️ 返回的是 HTML 网页内容 (可能为拦截页或失效页面)")
                print(f"  • 最终判定:   ❌ 不可播放 (非视频数据)")
            else:
                if chunk.startswith(b'FLV'):
                    video_type = "FLV 实时直播视频流 (Flash Video)"
                elif chunk.startswith(b'\x47'):
                    video_type = "MPEG-TS 传输流"
                elif b'ftyp' in chunk[:12]:
                    video_type = "MP4 / fMP4 流"
                else:
                    video_type = f"二进制媒体流 (首字节: {chunk[:8].hex()})"
                print(f"  • 流媒体格式: {video_type}")
                print(f"  • 最终判定:   ✅ 本地可无代理秒播 (Direct Playable)")

            print("-" * 68)
            print("💡 快捷播放测试命令:")
            print(f"   mpv \"{url}\"")
            print(f"   iina \"{url}\"")
            print(f"   ffprobe -v error -show_format \"{url}\"")
            print("-" * 68)

    except urllib.error.HTTPError as e:
        print(f"  • 请求失败:   HTTP {e.code} ({e.reason})")
        print(f"  • 最终判定:   ❌ 本地不可播放")
        print("-" * 68)
    except urllib.error.URLError as e:
        print(f"  • 请求失败:   网络错误 ({e.reason})")
        print(f"  • 最终判定:   ❌ 本地不可播放 (无法直连/超时)")
        print("-" * 68)
    except Exception as e:
        print(f"  • 请求失败:   {e}")
        print(f"  • 最终判定:   ❌ 本地不可播放")
        print("-" * 68)



def load_m3u_content(source: str, m3u_proxy: str = None):
    """从远程 URL 或本地文件读取 M3U 内容 (带连接重试、分块读取、GZIP 解压及系统 curl 引擎兜底)"""
    if source.startswith('http://') or source.startswith('https://'):
        print(f"🌐 正在从远程地址获取 M3U 播放列表: {source}")

        # 使用长连接与 gzip 压缩头，极大降低跨国 CDN 网络抖动导致的 IncompleteRead 概率
        headers = dict(DEFAULT_HEADERS)
        headers['Connection'] = 'keep-alive'
        headers['Accept-Encoding'] = 'gzip, deflate'

        # 优先使用配置的代理或 direct_opener
        opener = direct_opener
        if m3u_proxy:
            proxy_handler = urllib.request.ProxyHandler({'http': m3u_proxy, 'https': m3u_proxy})
            opener = urllib.request.build_opener(proxy_handler, https_handler)

        last_err = None
        for attempt in range(1, 4):
            try:
                req = urllib.request.Request(safe_url(source), headers=headers)
                with opener.open(req, timeout=25) as resp:
                    raw_data = bytearray()
                    while True:
                        try:
                            chunk = resp.read(65536)
                        except (http.client.IncompleteRead, socket.timeout) as ie:
                            if hasattr(ie, 'partial') and ie.partial:
                                raw_data.extend(ie.partial)
                            raise ie
                        if not chunk:
                            break
                        raw_data.extend(chunk)

                    # 自动处理 GZIP 解压缩
                    if resp.headers.get('Content-Encoding') == 'gzip' or (len(raw_data) > 2 and raw_data[:2] == b'\x1f\x8b'):
                        try:
                            raw_data = gzip.decompress(raw_data)
                        except Exception:
                            pass

                    for enc in ['utf-8', 'utf-8-sig', 'gbk', 'gb2312']:
                        try:
                            return raw_data.decode(enc)
                        except UnicodeDecodeError:
                            continue
                    return raw_data.decode('utf-8', errors='ignore')
            except Exception as e:
                last_err = e
                if attempt < 3:
                    time.sleep(1)
                    continue

        # 如果直连失败，自动调用系统 curl (继承系统网络环境/代理)
        print(f"⚠️ 直连下载遇到网络阻断 ({last_err})，自动调用系统 curl 引擎重试...")
        curl_cmd = ['curl', '-s', '-L', '--connect-timeout', '10', '-m', '35']
        if m3u_proxy:
            curl_cmd.extend(['-x', m3u_proxy])
        curl_cmd.append(source)
        try:
            res = subprocess.run(curl_cmd, capture_output=True, check=True)
            raw_data = res.stdout
            if not raw_data:
                raise RuntimeError("curl 下载内容为空")
            for enc in ['utf-8', 'utf-8-sig', 'gbk', 'gb2312']:
                try:
                    return raw_data.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw_data.decode('utf-8', errors='ignore')
        except Exception as curl_err:
            raise RuntimeError(
                f"加载远程 M3U 失败 (urllib: {last_err}; curl: {curl_err})。\n"
                f"💡 诊断提示: 目标 M3U 托管在海外 Cloudflare 服务器，直连遇到了 GFW SSL 阻断/握手超时。\n"
                f"   解决方案 1: 直接使用当前已下载好的本地文件进行测速:\n"
                f"              python3 check_m3u.py live.m3u ...\n"
                f"   解决方案 2: 若开启了代理软件 (如 Clash/Surge 端口 7890)，可加上 --m3u-proxy 参数下载列表:\n"
                f"              python3 check_m3u.py \"{source}\" --m3u-proxy http://127.0.0.1:7890 ..."
            )

    else:
        if not os.path.isfile(source):
            raise FileNotFoundError(f"未找到本地文件: {source}")
        with open(source, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()


def main():
    parser = argparse.ArgumentParser(
        description="IPTV M3U 本地连接性快速测试与筛选工具 (强制直连无代理 + 分组过滤)"
    )
    parser.add_argument("source", nargs="?", help="M3U 文件路径或远程 URL (留空则提示输入)")
    parser.add_argument("-o", "--output", help="筛选后的有效 M3U 保存路径 (默认: <输入源名>_playable.m3u)")
    parser.add_argument("-t", "--timeout", type=float, default=3.0, help="单频道超时时间 (秒，默认 3.0)")
    parser.add_argument("-c", "--concurrency", type=int, default=30, help="并发测试线程数 (默认 30)")
    parser.add_argument("--check-segment", action="store_true", help="深入测试 TS 切片下载 (更严格，耗时稍长)")
    parser.add_argument(
        "--sort", "--sort-by",
        nargs="?",
        const="speed",
        default=None,
        dest="sort_mode",
        choices=["speed", "fast", "name", "group-name", "group_name", "group", "group-speed", "group_speed"],
        help="输出排序方式: speed (默认, 最快优先), name (频道名自然序), group-name/group (按分组聚集且组内按名称), group-speed (按分组聚集且组内按速度)"
    )
    
    # 分组过滤与查看选项
    parser.add_argument(
        "-e", "--exclude-group", "--exclude-groups", "--remove-group", "--remove-groups",
        nargs="+",
        dest="exclude_groups",
        help="排除/移除指定分组的频道 (支持多个或逗号分隔，如: -e 虎牙 抖音 或 -e 虎牙,抖音)"
    )
    parser.add_argument(
        "-i", "--include-group", "--include-groups",
        nargs="+",
        dest="include_groups",
        help="仅保留指定分组的频道 (白名单模式，如: -i 央视 地方台)"
    )
    parser.add_argument(
        "--exact-group",
        action="store_true",
        help="分组名称完全匹配 (默认使用包含/模糊匹配)"
    )
    parser.add_argument(
        "--m3u-proxy",
        help="仅用于下载远程 M3U 播放列表的代理地址 (例如: http://127.0.0.1:7890)，所有频道测速仍严格无代理直连"
    )
    parser.add_argument(
        "-u", "--url",
        help="单独测试一个频道流地址 (输出包含重定向追踪、协议、延迟的深度诊断信息)"
    )
    parser.add_argument(
        "--list-groups",
        action="store_true",
        help="仅查看 M3U 中包含的分组列表及频道统计，不进行网络测速"
    )

    args = parser.parse_args()

    # 1. 显式单频道参数 (-u / --url)
    if args.url:
        diagnose_single_url(args.url, timeout=args.timeout)
        sys.exit(0)

    source = args.source
    if not source:
        try:
            source = input("请输入 M3U 链接/本地文件路径 (或单个频道 URL): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n已取消")
            sys.exit(0)

    if not source:
        print("❌ 未提供输入源，程序退出。")
        sys.exit(1)

    # 2. 智能自动识别：如果直接传入了单个直播流地址而非 M3U 文件
    if source.startswith(('rtp://', 'udp://', 'rtsp://')) or (
        source.startswith(('http://', 'https://')) and not any(source.split('?')[0].lower().endswith(ext) for ext in ('.m3u', '.m3u8', '.txt'))
    ):
        print("💡 检测到输入为单个频道流地址，自动进入单频道深度诊断模式...")
        diagnose_single_url(source, timeout=args.timeout)
        sys.exit(0)

    # 1. 加载 M3U
    try:
        content = load_m3u_content(source, m3u_proxy=args.m3u_proxy)
    except Exception as e:
        print(f"❌ 加载 M3U 失败: {e}")
        sys.exit(1)

    # 2. 解析
    header_lines, channels = parse_m3u(content)
    raw_total_channels = len(channels)
    print(f"📋 成功解析 M3U: 共找到 {raw_total_channels} 个频道条目")
    if raw_total_channels == 0:
        print("⚠️ 未在列表中解析出任何频道！")
        sys.exit(0)

    # 如果指定了 --list-groups，直接打印分组统计并退出
    if args.list_groups:
        group_counts = {}
        for ch in channels:
            grp = ch.group if ch.group else "未分组"
            group_counts[grp] = group_counts.get(grp, 0) + 1
        sorted_groups = sorted(group_counts.items(), key=lambda x: x[1], reverse=True)
        print(f"\n📑 列表中包含的所有分组 (共 {len(sorted_groups)} 个分组):")
        print("-" * 55)
        for grp, cnt in sorted_groups:
            print(f"  • {grp:<28} : {cnt:>4} 个频道")
        print("-" * 55)
        print("💡 提示: 可使用 -e/--exclude-group 排除不需要的分组，如: -e 虎牙 抖音")
        sys.exit(0)

    # 3. 分组过滤 (白名单 & 黑名单)
    exclude_patterns = parse_patterns(args.exclude_groups)
    include_patterns = parse_patterns(args.include_groups)
    excluded_channels_count = 0

    if include_patterns or exclude_patterns:
        filtered_channels = []
        for ch in channels:
            # 白名单检查 (如果指定了 include_group)
            if include_patterns and not is_group_matched(ch.group, include_patterns, args.exact_group):
                excluded_channels_count += 1
                continue
            # 黑名单检查 (如果指定了 exclude_group / remove_group)
            if exclude_patterns and is_group_matched(ch.group, exclude_patterns, args.exact_group):
                excluded_channels_count += 1
                continue
            filtered_channels.append(ch)

        if exclude_patterns:
            print(f"🚫 已排除分组频道: {excluded_channels_count} 个 (规则: {', '.join(exclude_patterns)})")
        if include_patterns:
            print(f"🎯 仅保留匹配分组: {', '.join(include_patterns)}")
        print(f"📋 过滤后待测试频道: {len(filtered_channels)} 个 (已跳过无需测试的 {excluded_channels_count} 个频道)")
        channels = filtered_channels

        if len(channels) == 0:
            print("⚠️ 经过分组筛选后，没有剩余的频道需要测试！")
            sys.exit(0)

    total_channels = len(channels)

    # 输出路径推导
    if args.output:
        out_path = args.output
    else:
        if source.startswith('http'):
            base_name = "online_iptv"
        else:
            base_name = os.path.splitext(os.path.basename(source))[0]
        out_path = f"{base_name}_playable.m3u"

    print(f"⚡ 开始测试本地直连可用性 (并发: {args.concurrency}, 超时: {args.timeout}s, 模式: {'切片深测' if args.check_segment else '索引快测'})...")
    print("-" * 68)

    valid_channels = []
    failed_count = 0
    checked_count = 0
    start_time = time.time()

    # 4. 多线程并发检测
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        future_map = {
            executor.submit(check_channel, ch, args.timeout, args.check_segment): ch
            for ch in channels
        }

        for future in as_completed(future_map):
            ch = future.result()
            checked_count += 1
            if ch.is_valid:
                valid_channels.append(ch)
                info = f"{ch.latency_ms}ms"
            else:
                failed_count += 1
                info = f"{ch.error_msg}"

            percent = (checked_count / total_channels) * 100
            disp_name = (ch.name[:12] + '..') if len(ch.name) > 14 else ch.name
            sys.stdout.write(
                f"\r\033[K[{checked_count}/{total_channels}] {percent:5.1f}% | 可用: {len(valid_channels)} | 失败: {failed_count} | 当前: {disp_name} ({info})"
            )
            sys.stdout.flush()

    sys.stdout.write("\n")
    total_duration = time.time() - start_time

    # 5. 排序处理 (如果指定了 --sort / --sort-by)
    if args.sort_mode:
        mode = args.sort_mode.lower().replace('_', '-')
        if mode in ('group-name', 'group'):
            # 保持分组在原源中的首次出现顺序，组内按名称自然序
            group_order = {}
            for ch in channels:
                grp = ch.group or "未分组"
                if grp not in group_order:
                    group_order[grp] = len(group_order)
            valid_channels.sort(
                key=lambda x: (group_order.get(x.group or "未分组", 9999), natural_sort_key(x.name))
            )
            print("🔀 排序规则: [按分组聚集排列，组内按频道名自然序 (拼音/数字)]")
        elif mode == 'group-speed':
            # 保持分组在原源中的首次出现顺序，组内按测速从快到慢
            group_order = {}
            for ch in channels:
                grp = ch.group or "未分组"
                if grp not in group_order:
                    group_order[grp] = len(group_order)
            valid_channels.sort(
                key=lambda x: (group_order.get(x.group or "未分组", 9999), x.latency_ms)
            )
            print("🔀 排序规则: [按分组聚集排列，组内按测速延迟从快到慢]")
        elif mode == 'name':
            valid_channels.sort(key=lambda x: natural_sort_key(x.name))
            print("🔀 排序规则: [全局按频道名自然序排列 (拼音/数字)]")
        elif mode in ('speed', 'fast'):
            valid_channels.sort(key=lambda x: x.latency_ms)
            print("🔀 排序规则: [全局按测速延迟从快到慢排列 (最低延迟优先)]")

    # 6. 生成结果 M3U 文件
    out_lines = list(header_lines)
    for ch in valid_channels:
        if ch.raw_lines:
            out_lines.extend(ch.raw_lines)
        else:
            out_lines.append(f"#EXTINF:-1,{ch.name}")
        out_lines.append(ch.url)

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out_lines) + '\n')

    # 7. 统计报告
    print("-" * 68)
    print("📊 本地直连测试完成报告:")
    print(f"  • 源总频道数: {raw_total_channels} 个")
    if excluded_channels_count > 0:
        print(f"  • 排除分组:   {excluded_channels_count} 个频道 (已直接跳过无需测试)")
    print(f"  • 实际测试:   {total_channels} 个频道")
    print(f"  • 直连可用:   {len(valid_channels)} 个 (直连有效率: {len(valid_channels)/total_channels*100:.1f}%)")
    print(f"  • 失效/阻断: {failed_count} 个")
    print(f"  • 耗时:       {total_duration:.2f} 秒 (平均 {(total_duration/total_channels):.3f}s/频道)")
    print(f"  • 输出文件:   {os.path.abspath(out_path)}")
    print("-" * 68)


if __name__ == '__main__':
    main()
