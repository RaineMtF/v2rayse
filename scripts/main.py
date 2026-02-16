import yaml
import requests
import os
import urllib.parse
import time
import shutil

from freeproxy import download_freeproxy

import sys
from seleniumbase import Driver

def download_url_to_file(url, filename):
    # uc=True: 开启反爬虫探测绕过模式
    # headless=True: 在 GitHub Actions 等无界面环境运行必须开启
    driver = Driver(uc=True, headless=True)
    
    try:
        print(f"[Sub] 正在访问: {url}")
        # driver.get(url)
        driver.uc_open_with_reconnect(url, reconnect_time=5)
        
        # 显式等待：防止页面内容还没加载完就保存
        # 这里默认等待 5 秒，或者你可以根据需要等待某个特定元素
        # driver.sleep(5) 
        driver.uc_gui_handle_captcha()
        # 获取渲染后的页面源码
        page_source = driver.page_source
        
        with open(filename, "w", encoding="utf-8") as f:
            f.write(page_source)
            
        print(f"[Sub] 保存成功: {filename}")
    except Exception as e:
        print(f"[Sub] 发生错误: {e}")
        sys.exit(1) # 报错退出，让 Github Actions 标记为失败
    finally:
        driver.quit()

def load_config(config_path):
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def download_config(endpoint, target_info, url_list, config_url, extra_params, base_dir):
    target_name = list(target_info.keys())[0]
    target_config = target_info[target_name]
    
    target = target_config['target']
    file_name = target_config['file']
    
    encoded_urls = urllib.parse.quote("|".join(url_list), safe='')
    api_url = f"https://{endpoint}/sub?target={target}&url={encoded_urls}"
    
    if config_url:
        api_url += f"&config={urllib.parse.quote(config_url, safe='')}"
    
    for key, value in extra_params.items():
        if isinstance(value, bool):
            api_url += f"&{key}={str(value).lower()}"
        else:
            api_url += f"&{key}={value}"
            
    output_dir = os.path.join(base_dir, 'configs')
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    if target in ['clash', 'clashr']:
        file_name_final = f"{target_name}.yml"
    else:
        file_name_final = file_name
        
    file_path = os.path.join(output_dir, file_name_final)
    
    print(f"[Sub] Downloading {target_name} to {file_path}")
    download_url_to_file(api_url, file_path)

def merge_files(merge_list, base_dir):
    if not merge_list:
        return
    
    configs_dir = os.path.join(base_dir, 'configs')
    print(f"[Merge] Starting file merging in {configs_dir}")
    
    for entry in merge_list:
        if not isinstance(entry, dict):
            continue
            
        for target_file, source_files in entry.items():
            target_path = os.path.join(configs_dir, target_file)
            print(f"[Merge] Merging {source_files} into {target_file}")
            
            unique_lines = []
            seen = set()
            for src in source_files:
                src_path = os.path.join(configs_dir, src)
                if os.path.exists(src_path):
                    with open(src_path, 'r', encoding='utf-8') as f:
                        for line in f:
                            clean_line = line.strip()
                            if clean_line and clean_line not in seen:
                                unique_lines.append(clean_line)
                                seen.add(clean_line)
                else:
                    print(f"[Merge] Warning: Source file {src} not found.")
            
            if unique_lines:
                with open(target_path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(unique_lines) + '\n')
                print(f"[Merge] Successfully created {target_file} (Total: {len(unique_lines)} unique lines)")
            else:
                print(f"[Merge] Skip {target_file}: No content found in source files.")

def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # Clean output directory
    configs_dir = os.path.join(base_dir, 'configs')
    if os.path.exists(configs_dir):
        print(f"[Main] Cleaning output directory: {configs_dir}")
        shutil.rmtree(configs_dir)
    os.makedirs(configs_dir, exist_ok=True)

    config = load_config(os.path.join(base_dir, 'config.yml'))
    endpoint = config.get('endpoint', 'api.wcc.best').strip()
    url_list = [u.strip() for u in config.get('url', []) if u.strip()]
    config_url = config.get('config', '').strip()
    extra_params = config.get('extra', {})
    target_list = config.get('target_list', [])
    
    for target_info in target_list:
        download_config(endpoint, target_info, url_list, config_url, extra_params, base_dir)
        time.sleep(1)

    freeproxy_list = config.get('freeproxy_list', [])
    for fp_config in freeproxy_list:
        download_freeproxy(fp_config, base_dir)
        time.sleep(1)

    merge_list = config.get('merge_list', [])
    merge_files(merge_list, base_dir)

if __name__ == "__main__":
    main()
