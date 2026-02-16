import yaml
import requests
import os
import urllib.parse
import time

from scraper import download_url_to_file
from freeproxy import download_freeproxy

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
        
    # headers = {
    #     'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    # }
    
    # print(f"Downloading {target_name} from {api_url}")
    
    # try:
    #     response = requests.get(api_url, headers=headers, timeout=60)
    #     response.raise_for_status()
        
    #     output_dir = os.path.join(base_dir, 'configs')
    #     if not os.path.exists(output_dir):
    #         os.makedirs(output_dir)
            
    #     if target in ['clash', 'clashr']:
    #         file_name_final = f"{target_name}.yml"
    #     else:
    #         file_name_final = file_name
            
    #     file_path = os.path.join(output_dir, file_name_final)
            
    #     with open(file_path, 'w', encoding='utf-8') as f:
    #         f.write(response.text)
    #     print(f"Successfully saved {target_name} to {file_path}")
        
    # except Exception as e:
    #     print(f"Error downloading {target_name}: {e}")

def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

if __name__ == "__main__":
    main()
