"""第一阶段：从 freeproxy.world 单线程顺序抓取代理"""

from __future__ import annotations

import time
import urllib.parse
from typing import Optional, Set

from seleniumbase import Driver

from parser import check_blocked, parse_proxy_page
from models import Proxy


class _PageResult:
    """页面下载结果内部封装"""

    def __init__(self, configs: list[str] | None = None, is_empty: bool = False):
        self.configs = configs or []
        self.is_empty = is_empty

    @property
    def success(self) -> bool:
        return bool(self.configs)


def _parse_proxy_str(proxy_str: str, category: str = "") -> Proxy:
    """将 parser 产出的 'type://ip:port#country,%20city' 解析为 Proxy 对象"""
    try:
        url_part, fragment = proxy_str.split("#", 1)
    except ValueError:
        url_part = proxy_str
        fragment = ""

    parsed = urllib.parse.urlparse(url_part)
    proxy_type = parsed.scheme
    ip = parsed.hostname or ""
    port = parsed.port or 0

    country = "Unknown"
    city = ""
    if fragment:
        try:
            decoded = urllib.parse.unquote(fragment)
            parts = decoded.split(",")
            country = parts[0].strip() if parts else "Unknown"
            city = parts[1].strip() if len(parts) > 1 else ""
        except Exception:
            pass

    return Proxy(
        proxy_type=proxy_type,
        ip=ip,
        port=port,
        country=country,
        city=city,
        raw_str=proxy_str,
        category=category,
    )


def _fetch_page(
    page: int,
    base_url: str,
    params: dict,
    *,
    max_retries: int = 10,
    base_delay: float = 5.0,
) -> _PageResult:
    """
    下载单个页面，带指数退避重试。

    被阻断、网络错误等一律在内部重试；空页则直接返回。
    每次尝试都使用新的 Driver 实例，结束后关闭。
    """
    current_params = params.copy()
    current_params["page"] = page
    query_string = urllib.parse.urlencode(current_params)
    target_url = f"{base_url}?{query_string}"

    print(f"[Fetcher] 开始抓取第 {page} 页: {target_url}")

    last_exception: Optional[BaseException] = None

    for attempt in range(max_retries):
        if attempt > 0:
            wait_time = base_delay * (2 ** (attempt - 1))
            print(
                f"[Fetcher] 第 {page} 页第 {attempt + 1}/{max_retries} 次尝试，"
                f"等待 {wait_time:.1f}s 后重试..."
            )
            time.sleep(wait_time)

        driver = None
        try:
            driver = Driver(uc=True, headless=True)
            driver.uc_open_with_reconnect(target_url, reconnect_time=5)
            driver.uc_gui_handle_captcha()

            html_content = driver.page_source
            page_title = driver.title

            page_configs = parse_proxy_page(html_content)

            if page_configs:
                print(
                    f"[Fetcher] 第 {page} 页成功抓取 {len(page_configs)} 个代理 "
                    f"(尝试 {attempt + 1}/{max_retries})"
                )
                return _PageResult(configs=page_configs)

            if check_blocked(html_content, page_title):
                print(f"[Fetcher] 第 {page} 页触发防火墙阻断，准备重试")
                last_exception = RuntimeError("防火墙阻断")
                continue

            print(f"[Fetcher] 第 {page} 页为空页")
            return _PageResult(is_empty=True)

        except Exception as exc:
            last_exception = exc
            print(f"[Fetcher] 第 {page} 页尝试 {attempt + 1}/{max_retries} 失败: {exc}")
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

    print(
        f"[Fetcher] 第 {page} 页所有 {max_retries} 次尝试都失败，"
        f"最后错误: {last_exception}"
    )
    return _PageResult()


def run_fetcher(freeproxy_list, raw_queue):
    """
    单线程顺序抓取 proxyworld 多个分类。

    - 按 (type, ip, port) 全局去重
    - 提取到新代理后写入 raw_queue
    - 全部完成后向 raw_queue 放入哨兵 None
    """
    seen: Set[tuple] = set()

    for fp_config in freeproxy_list:
        if not isinstance(fp_config, dict):
            continue

        name = list(fp_config.keys())[0]
        config = fp_config[name]
        base_url = "https://www.freeproxy.world/"

        print(f"[Fetcher] 开始抓取分类: {name}")

        page = 0
        empty_streak = 0

        while True:
            page += 1
            if empty_streak >= 2:
                print(
                    f"[Fetcher] {name}: 已连续 {empty_streak} 页空页，停止抓取"
                )
                break

            result = _fetch_page(page, base_url, config)

            if result.is_empty:
                empty_streak += 1
                continue

            if result.configs:
                for proxy_str in result.configs:
                    proxy = _parse_proxy_str(proxy_str, category=name)
                    if proxy.identity not in seen:
                        seen.add(proxy.identity)
                        raw_queue.put(proxy)
                        # print(f"[Fetcher] 新代理: {proxy.identity}")
                    else:
                        print(f"[Fetcher] 重复跳过: {proxy.identity}")
                empty_streak = 0
                continue

            # 所有重试均失败，不视为空页
            empty_streak = 0
            print(f"[Fetcher] 第 {page} 页所有重试失败，跳过")

    raw_queue.put(None)
    print("[Fetcher] 所有分类抓取完成")
