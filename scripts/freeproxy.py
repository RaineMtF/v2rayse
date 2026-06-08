"""FreeProxy 下载模块，单进程顺序下载"""

from __future__ import annotations

import os
import time
from typing import Optional
import urllib.parse

from seleniumbase import Driver

from parser import check_blocked, parse_proxy_page


class _PageResult:
    """页面下载结果的内部封装"""

    def __init__(self, configs: list[str] | None = None, is_empty: bool = False) -> None:
        self.configs = configs or []
        self.is_empty = is_empty

    @property
    def success(self) -> bool:
        return bool(self.configs)


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

    Args:
        page: 页码。
        base_url: 基础 URL。
        params: 查询参数字典（会被复制）。
        max_retries: 最大重试次数。
        base_delay: 重试基础等待秒数。

    Returns:
        _PageResult 对象：
            - configs: 该页提取的代理配置列表
            - is_empty: 是否为空页（页面正常但无数据）
    """
    current_params = params.copy()
    current_params["page"] = page
    query_string = urllib.parse.urlencode(current_params)
    target_url = f"{base_url}?{query_string}"

    print(f"[Freeproxy] 开始抓取第 {page} 页: {target_url}")

    last_exception: Optional[BaseException] = None

    for attempt in range(max_retries):
        # 指数退避（第一次不等待）
        if attempt > 0:
            wait_time = base_delay * (2 ** (attempt - 1))
            print(
                f"[Freeproxy] 第 {page} 页第 {attempt + 1}/{max_retries} 次尝试，"
                f"等待 {wait_time}s 后重试..."
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
                    f"[Freeproxy] 第 {page} 页成功抓取 {len(page_configs)} 个代理 "
                    f"(尝试 {attempt + 1}/{max_retries})"
                )
                return _PageResult(configs=page_configs)

            # 没有提取到数据 → 检查是否被阻断
            if check_blocked(html_content, page_title):
                print(f"[Freeproxy] 第 {page} 页触发防火墙阻断，准备重试")
                last_exception = RuntimeError("防火墙阻断")
                continue  # 进入下一次重试

            # 正常空页
            print(f"[Freeproxy] 第 {page} 页为空页")
            return _PageResult(is_empty=True)

        except Exception as exc:
            last_exception = exc
            print(
                f"[Freeproxy] 第 {page} 页尝试 {attempt + 1}/{max_retries} 失败: {exc}"
            )
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

    # 所有重试均失败
    print(
        f"[Freeproxy] 第 {page} 页所有 {max_retries} 次尝试都失败，"
        f"最后错误: {last_exception}"
    )
    return _PageResult()


def download_freeproxy(
    config_map: dict,
    base_dir: str,
    *,
    max_retries: int = 10,
    retry_delay: float = 5.0,
) -> None:
    """
    单进程顺序下载 freeproxy 代理。

    策略：
    - 按页码顺序逐页下载
    - 被阻断、网络错误等在页内重试
    - 连续两页为空页时提前终止，其余情况一直抓完

    Args:
        config_map: 配置映射，包含名称和参数
        base_dir: 基础目录路径
        max_retries: 每页最大重试次数
        retry_delay: 重试基础等待秒数
    """
    name = list(config_map.keys())[0]
    config = config_map[name]
    base_url = "https://www.freeproxy.world/"

    print(
        f"[Freeproxy] 单进程顺序下载，无页数上限，"
        f"每页最多重试 {max_retries} 次，连续两页为空页后停止"
    )

    all_v2ray_configs: list[str] = []
    empty_streak = 0
    stopped_early = False
    page = 0

    while True:
        page += 1
        if empty_streak >= 2:
            print(f"[Freeproxy] 已连续 {empty_streak} 页空页，停止抓取")
            stopped_early = True
            break

        result = _fetch_page(
            page=page,
            base_url=base_url,
            params=config,
            max_retries=max_retries,
            base_delay=retry_delay,
        )

        if result.is_empty:
            empty_streak += 1
            continue

        if result.success:
            all_v2ray_configs.extend(result.configs)
            empty_streak = 0
            print(f"[Freeproxy] 第 {page} 页已加入 {len(result.configs)} 个代理")
            continue

        # 所有重试均失败，但不视为空页，直接继续
        empty_streak = 0
        print(f"[Freeproxy] 第 {page} 页所有重试失败，跳过")

    # 保存结果
    output_dir = os.path.join(base_dir, "configs")
    os.makedirs(output_dir, exist_ok=True)

    file_name = config.get("file", f"{name}.txt")
    file_path = os.path.join(output_dir, file_name)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(all_v2ray_configs))

    # 输出统计信息
    total = len(all_v2ray_configs)
    if stopped_early:
        print(
            f"[Freeproxy] 已完成: {name} -> {file_path} "
            f"(共 {total} 个代理，因连续空页提前终止)"
        )
    else:
        print(
            f"[Freeproxy] 已完成: {name} -> {file_path} "
            f"(共 {total} 个代理，所有页面已处理)"
        )


def save_to_file(configs: list[str]) -> None:
    """将代理列表保存到 all_config.txt（兼容性保留）"""
    with open("all_config.txt", "w", encoding="utf-8") as f:
        if configs:
            f.write("\n".join(configs) + "\n")
    print(f"任务完成，总计 {len(configs)} 个代理，已保存到 all_config.txt")
