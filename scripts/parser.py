"""HTML 解析模块，用于解析 freeproxy.world 的页面内容"""

from bs4 import BeautifulSoup
import re
import urllib.parse


def parse_proxy_page(html_content: str) -> list[str]:
    """
    解析 freeproxy.world 页面 HTML，提取代理配置列表

    Args:
        html_content: 页面 HTML 字符串

    Returns:
        代理配置字符串列表，格式为: "type://ip:port#country,%20city"
    """
    soup = BeautifulSoup(html_content, 'lxml')

    tables = soup.find_all('table')
    page_configs = []

    for table in tables:
        rows = table.find_all('tr')
        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 4:
                continue

            try:
                # 1. IP
                ip = cells[0].get_text(strip=True)
                if not re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', ip):
                    continue

                # 2. Port
                port = cells[1].get_text(strip=True)

                # 3. Country Code
                country_code = "Unknown"
                country_link = cells[2].find('a', href=True)
                if country_link:
                    match = re.search(r'country=([A-Z]+)', country_link['href'])
                    if match:
                        country_code = match.group(1)

                # 4. City
                city = cells[3].get_text(strip=True)
                encoded_city = urllib.parse.quote(city)

                # 5. Type (priority: socks5 > socks4 > https > http)
                type_cell = cells[5]
                type_links = type_cell.find_all('a', href=True)
                type_list = []
                for link in type_links:
                    href = link.get('href', '')
                    match = re.search(r'type=([a-z0-9]+)', href)
                    if match:
                        type_list.append(match.group(1).lower())

                # 按照优先级选择类型
                priority = ['socks5', 'socks4', 'https', 'http']
                selected_type = 'http'  # 默认
                for t in priority:
                    if t in type_list:
                        selected_type = t
                        break

                # 拼接格式
                proxy_str = f"{selected_type}://{ip}:{port}#{country_code},%20{encoded_city}"
                page_configs.append(proxy_str)

            except Exception:
                continue

    return page_configs


def check_blocked(html_content: str, page_title: str = "") -> bool:
    """
    检查页面是否被防火墙阻断（Cloudflare、WAF 等）

    Args:
        html_content: 页面 HTML 字符串
        page_title: 页面标题

    Returns:
        是否被阻断
    """
    if not html_content:
        return False

    # 标题检测（传入 page_title 保留原始大小写）
    if "Cloudflare" in page_title or "Just a moment" in page_title:
        return True

    # HTML 内容检测（不区分大小写以覆盖更多变体）
    html_lower = html_content.lower()
    indicators = [
        "access denied",
        "accessdenied",
        "cf-ray:",
        'id="cf-wrapper"',
        'id="cf-challenge-running"',
        "window._cf_ch",
        "cloudflare-challenge",
        "checking your browser",
    ]
    return any(indicator in html_lower for indicator in indicators)
