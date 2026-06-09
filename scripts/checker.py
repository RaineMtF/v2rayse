"""第二阶段：多线程并发验证代理可用性"""

from __future__ import annotations

import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

from models import Proxy, ValidateResult


# ---------------------------------------------------------------------------
# 统计计数器
# ---------------------------------------------------------------------------

class _CheckerStats:
    """Checker 运行期统计"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.submitted = 0       # 已提交给进程池的代理总数
        self.completed = 0       # 已从进程池拿到结果的代理总数
        self.success = 0         # 最终可用数
        self.fail_google = 0     # 败在 google generate204
        self.fail_cloudflare = 0 # 败在 cloudflare
        self.fail_openssh = 0    # 败在 openssh
        self.fail_exception = 0  # 进程内抛异常

    @property
    def pending(self) -> int:
        with self._lock:
            return self.submitted - self.completed

    def record_ok(self) -> None:
        with self._lock:
            self.completed += 1
            self.success += 1

    def record_fail(self, step: str) -> None:
        with self._lock:
            self.completed += 1
            attr = f"fail_{step}"
            if hasattr(self, attr):
                setattr(self, attr, getattr(self, attr) + 1)

    def record_exception(self) -> None:
        with self._lock:
            self.completed += 1
            self.fail_exception += 1

    def summary(self) -> str:
        with self._lock:
            failed = self.completed - self.success
            return (
                f"[CheckerStats] "
                f"待完成={self.pending}, 已完成={self.completed}, 成功={self.success}, "
                f"失败={failed} "
                f"(google={self.fail_google}, cloudflare={self.fail_cloudflare}, "
                f"openssh={self.fail_openssh}, exception={self.fail_exception})"
            )


def _logger_thread(stats: _CheckerStats, interval: int, stop_event: threading.Event):
    """后台 daemon 线程，定期打印统计信息"""
    while not stop_event.is_set():
        time.sleep(interval)
        print(stats.summary())


# ---------------------------------------------------------------------------
# 代理验证逻辑（在子进程内运行，必须保证可被 pickle）
# ---------------------------------------------------------------------------

def _check_single(proxy: Proxy) -> ValidateResult:
    """
    验证单个代理的可用性。

    验证流程（不进行重试，每步严格超时 5.0 秒）：
    1. Google generate204 — 连通性 (严格 204 校验)
    2. Cloudflare         — 稳定性 (严格 204 校验)
    3. OpenSSH.org (TLS)  — 高安全要求网站访问能力 (严格 200 校验，非 200 降级为警告)
    """
    proxy_url = proxy.url
    proxies = {"http": proxy_url, "https": proxy_url}
    result = ValidateResult(proxy=proxy, available=False)
    TIMEOUT_SEC = 5.0

    # Step 1: Google generate204
    try:
        start = time.perf_counter()
        resp = requests.get(
            "https://clients3.google.com/generate_204",
            proxies=proxies,
            timeout=TIMEOUT_SEC,
        )
        result.google_204_ms = round((time.perf_counter() - start) * 1000, 2)
        if resp.status_code != 204:
            result.error = f"google_204 status {resp.status_code} (Expected 204)"
            result.fail_step = "google"
            return result
    except Exception as exc:
        result.error = repr(exc)
        result.fail_step = "google"
        return result

    # Step 2: Cloudflare
    try:
        start = time.perf_counter()
        resp = requests.get(
            "https://cp.cloudflare.com",
            proxies=proxies,
            timeout=TIMEOUT_SEC,
        )
        result.cloudflare_ms = round((time.perf_counter() - start) * 1000, 2)
        if resp.status_code != 204:
            result.error = f"cloudflare status {resp.status_code} (Expected 204)"
            result.fail_step = "cloudflare"
            return result
    except Exception as exc:
        result.error = repr(exc)
        result.fail_step = "cloudflare"
        return result

    # Step 3: OpenSSH.org (高 TLS 要求)
    try:
        resp = requests.get(
            "https://www.openssh.org/",
            proxies=proxies,
            timeout=TIMEOUT_SEC,
        )
        if resp.status_code == 200:
            result.openssh_ok = True
            result.available = True
        else:
            result.error = f"openssh status {resp.status_code} (Expected 200)"
            result.openssh_ok = False
            result.available = True
    except Exception as exc:
        result.error = repr(exc)
        result.fail_step = "openssh"
        return result

    return result


# ---------------------------------------------------------------------------
# 供多线程直接调用的入口
# ---------------------------------------------------------------------------

def run_checker(raw_queue, result_queue, max_workers: int | None = None, log_interval: int = 5):
    """
    从 raw_queue 消费 Proxy 对象，使用多线程并发验证，
    将 ValidateResult 写入 result_queue，完成后发送哨兵 None。
    """
    if max_workers is None:
        max_workers = 128

    print(f"[Checker] 启动验证服务，线程池大小: {max_workers}")

    stats = _CheckerStats()
    logger_stop = threading.Event()
    logger = threading.Thread(
        target=_logger_thread,
        args=(stats, log_interval, logger_stop),
        daemon=True,
    )
    logger.start()

    try:
        # 批量从 raw_queue 消费所有 Proxy
        proxies = []
        while True:
            proxy = raw_queue.get()
            if proxy is None:
                break
            stats.submitted += 1
            proxies.append(proxy)

        total = len(proxies)
        if total == 0:
            print("[Checker] 无代理需要验证")
            return

        print(f"[Checker] 开始验证 {total} 个代理")

        # 合适的 chunksize 减少 IPC 开销（每个 worker 一轮处理多个任务）
        chunksize = max(1, total // (max_workers * 4)) if total >= max_workers else 1

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # imap_unordered 实时返回结果，无需手动管理 futures dict
            for result in executor.imap_unordered(_check_single, proxies, chunksize=chunksize):
                result_queue.put(result)

                # 更新统计
                if result.available:
                    stats.record_ok()
                else:
                    if result.fail_step:
                        stats.record_fail(result.fail_step)
                    else:
                        stats.record_exception()

                identity = getattr(result.proxy, "identity", "unknown")
                status = "✅ 可用" if result.available else f"❌ 不可用 ({result.error})"
                print(f"[Checker] {identity} -> {status}")
    finally:
        logger_stop.set()
        logger.join(timeout=1)

    result_queue.put(None)
    print(stats.summary())
    print("[Checker] 所有代理验证完成")
