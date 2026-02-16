import os
from seleniumbase import Driver
from bs4 import BeautifulSoup
import re
import urllib.parse

def download_freeproxy(config_map, base_dir):
    name = list(config_map.keys())[0]
    config = config_map[name]
    base_url = "https://www.freeproxy.world/"
    all_v2ray_configs = []

    page_max = config.get('max', 50)

    # 初始化 SeleniumBase Driver (UC 模式)
    # headless=True 在 GitHub Actions 环境是必须的
    driver = Driver(uc=True, headless=True)
    
    try:
        for page in range(1, page_max + 1):
            # 构造参数
            current_params = config.copy()
            current_params['page'] = page
            query_string = urllib.parse.urlencode(current_params)
            target_url = f"{base_url}?{query_string}"
            
            print(f"[Freeproxy] 正在抓取第 {page} 页: {target_url}")

            # 使用 uc_open_with_reconnect 绕过 Cloudflare 等待界面
            driver.uc_open_with_reconnect(target_url, reconnect_time=5)
            
            # 如果遇到验证，可以尝试这个命令（但在 headless 下效果有限）
            driver.uc_gui_handle_captcha() 

            # 获取渲染后的源码
            html_content = driver.page_source
            soup = BeautifulSoup(html_content, 'html.parser')
            
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

                        # 拼接格式
                        proxy_str = f"socks://Og@{ip}:{port}#{country_code},%20{encoded_city}"
                        page_configs.append(proxy_str)
                        
                    except Exception:
                        continue

            if not page_configs:
                # 检查是否被完全封禁
                if "Cloudflare" in driver.title or "Access Denied" in html_content:
                    print(f"[Freeproxy] 第 {page} 页触发了防火墙阻断。")
                else:
                    print(f"[Freeproxy] 第 {page} 页未提取到有效数据，可能已到末页。")
                break
            
            print(f"[Freeproxy] 第 {page} 页成功抓取 {len(page_configs)} 个代理")
            all_v2ray_configs.extend(page_configs)
            
            # UC 模式下，库会自动处理部分休眠，但手动微调可以更稳
            # driver.sleep(2)

    finally:
        driver.quit()

    output_dir = os.path.join(base_dir, 'configs')
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    file_name = config.get('file', f"{name}.txt")
    file_path = os.path.join(output_dir, file_name)
        
    with open(file_path, "w", encoding="utf-8") as f:
        f.write('\n'.join(all_v2ray_configs))
    
    print(f"[Freeproxy] Downloaded {name} to {file_path}")

def save_to_file(configs):
    with open('all_config.txt', 'w', encoding='utf-8') as f:
        if configs:
            f.write('\n'.join(configs) + '\n')
    print(f"任务完成，总计 {len(configs)} 个代理，已保存到 all_config.txt")
