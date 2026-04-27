import os
import urllib.request
from urllib.parse import quote

m3u_file = "IPTV_Enhanced_change.m3u"
save_dir = "tv_icons"
# 使用 jsdelivr CDN 加速下载，避免 GitHub Raw 国内下载超时
base_url = "https://fastly.jsdelivr.net/gh/fanmingming/live@main/tv/"

os.makedirs(save_dir, exist_ok=True)

with open(m3u_file, 'r', encoding='utf-8') as f:
    lines = f.readlines()

channels = set()
for line in lines:
    if line.startswith("#EXTINF:"):
        # 获取逗号后面的频道名 (处理了已经带有 tvg-logo 标签的情况)
        channel_name = line.strip().split(',')[-1]
        if channel_name:
            channels.add(channel_name)

print(f"共提取到 {len(channels)} 个不重复的频道，开始下载...")

for name in channels:
    # 拼接 URL，并对中文名称进行 URL 编码
    url = base_url + quote(name + ".png")
    save_path = os.path.join(save_dir, f"{name}.png")
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp, open(save_path, 'wb') as out:
            out.write(resp.read())
        print(f"[成功] {name}.png")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"[跳过] 仓库中没有此图标: {name}.png")
    except Exception as e:
        print(f"[失败] 网络错误: {name}.png ({e})")

print(f"处理完毕！图标已保存在 {save_dir} 文件夹中。")