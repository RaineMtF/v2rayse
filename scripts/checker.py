"""第二阶段：多进程并发验证代理可用性"""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import requests

from models import Proxy, ValidateResult


def _check_single(proxy: Proxy) -> ValidateResult:
    """
    验证单个代理的可用性。

    验证流程（每步超时 5 秒）：
    1. Google generate204 — 连通性
    2. Cloudflare          — 稳定性
    3. OpenSSH.org (TLS)  — 高安全要求网站访问能力
    """
    proxy_url = proxy.url
    proxies = {
        "http": proxy_url,
        "https": proxy_url,
    }

    result = ValidateResult(proxy=proxy, available=False)

    try:
        # Step 1: Google generate204
        start = time.perf_counter()
        resp = requests.get(
            "http://clients3.google.com/generate_204",
            proxies=proxies,
            timeout=5,
        )
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        if resp.status_code != 204:
            result.error = f"google_204 status {resp.status_code}"
            return result
        result.google_204_ms = elapsed

        # Step 2: Cloudflare
        start = time.perf_counter()
        resp = requests.get(
            "http://cp.cloudflare.com",
            proxies=proxies,
            timeout=5,
        )
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        if resp.status_code != 204:
            result.error = f"cloudflare status {resp.status_code}"
            return result
        result.cloudflare_ms = elapsed

        # Step 3: OpenSSH.org (高 TLS 要求)
        resp = requests.get(
            "https://www.openssh.org/",
            proxies=proxies,
            timeout=5,
        )
        if resp.status_code == 200:
            result.openssh_ok = True
            result.available = True
        else:
            result.error = f"openssh status {resp.status_code}"

    except requests.exceptions.ProxyError as exc:
        result.error = f"proxy_error: {exc}"
    except requests.exceptions.Timeout:
        result.error = "timeout"
    except requests.exceptions.ConnectionError as exc:
        result.error = f"connection_error: {exc}"
    except Exception as exc:
        result.error = f"unexpected_error: {exc}"

    return result


# ---------------------------------------------------------------------------
# 供 multiprocessing.Process 进程内直接调用的入口
# ---------------------------------------------------------------------------

def run_checker(raw_queue, result_queue, max_workers: int = 16):
    """
    从 raw_queue 消费 Proxy 对象，使用 16 进程池并发验证，
    将 ValidateResult 写入 result_queue，完成后发送哨兵 None。
    """
    print(f"[Checker] 启动验证服务，进程池大小: {max_workers}")

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # futures 映射: Future -> Proxy
        futures: dict = {}

        while True:
            proxy = raw_queue.get()
            if proxy is None:
                break

            future = executor.submit(_check_single, proxy)
            futures[future] = proxy

            # 消费已完成的任务，避免内存无限膨胀
            done = [f for f in list(futures.keys()) if f.done()]
            for f in done:
                _consume_future(f, futures, result_queue)

        # 等待剩余全部完成
        for f in as_completed(futures):
            _consume_future(f, futures, result_queue)

    # 发送完成哨兵
    result_queue.put(None)
    print("[Checker] 所有代理验证完成")


def _consume_future(future, futures, result_queue):
    """安全地从 Future 取出结果并写入 result_queue，处理异常"""
    proxy = futures.pop(future, None)
    if proxy is None:
        return

    try:
        res = future.result()
        result_queue.put(res)
        status = "可用" if res.available else f"不可用 ({res.error})"
        print(f"[Checker] {res.proxy.identity} -> {status}")
    except Exception as exc:
        from models import ValidateResult  # noqa: reimport for safety
        result_queue.put(
            ValidateResult(proxy=proxy, available=False, error=str(exc))
        )
        print(f"[Checker] {proxy.identity} -> 异常: {exc}")
