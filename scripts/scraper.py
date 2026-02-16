import sys
from seleniumbase import Driver

def download_url_to_file(url, filename):
    # uc=True: 开启反爬虫探测绕过模式
    # headless=True: 在 GitHub Actions 等无界面环境运行必须开启
    driver = Driver(uc=True, headless=True)
    
    try:
        print(f"正在访问: {url}")
        driver.get(url)
        
        # 显式等待：防止页面内容还没加载完就保存
        # 这里默认等待 5 秒，或者你可以根据需要等待某个特定元素
        driver.sleep(5) 
        
        # 获取渲染后的页面源码
        page_source = driver.get_page_source()
        
        with open(filename, "w", encoding="utf-8") as f:
            f.write(page_source)
            
        print(f"保存成功: {filename}")
    except Exception as e:
        print(f"发生错误: {e}")
        sys.exit(1) # 报错退出，让 Github Actions 标记为失败
    finally:
        driver.quit()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python scraper.py <URL> <文件名>")
        sys.exit(1)
    
    target_url = sys.argv[1]
    target_file = sys.argv[2]
    download_url_to_file(target_url, target_file)
