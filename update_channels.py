import os
from urllib.parse import quote

# 你的频道别名映射表
mapping = {
    "新闻综合HD": "上视新闻",
    "都市频道HD": "上海都市",
    "第一财经HD": "上海第一财经",
    "CCTV-4K": "CCTV4K",
    "中国教育-1HD": "CETV1",
    "中国教育-2": "CETV2",
    "中国教育-4HD": "CETV4"
}

# 基础图标路径（建议使用 jsdelivr CDN 加速）
base_url = "https://fastly.jsdelivr.net/gh/fanmingming/live@main/tv/"
m3u_file = "IPTV_Enhanced_change.m3u"
output_file = "IPTV_Enhanced_updated.m3u"

def add_logo_to_m3u():
    with open(m3u_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []
    for line in lines:
        if line.startswith("#EXTINF:"):
            parts = line.strip().split(',')
            channel_name = parts[-1].strip()
            
            # 如果在映射表中，使用映射名称找图标；否则直接使用频道名
            icon_name = mapping.get(channel_name, channel_name)
            logo_url = base_url + quote(icon_name + ".png")
            
            # 检查是否已经存在 tvg-logo 标签，避免重复添加
            if 'tvg-logo=' not in parts[0]:
                parts[0] = parts[0] + f' tvg-logo="{logo_url}"'
                line = ",".join(parts) + "\n"
                
        new_lines.append(line)

    with open(output_file, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
        
    print(f"处理完成！已生成带台标的播放列表：{output_file}")

if __name__ == "__main__":
    add_logo_to_m3u()