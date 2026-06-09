"""第二阶段：多进程并发验证代理可用性"""

from __future__ import annotations

import sys
import time
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed

import requests

from models import Proxy, ValidateResult


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _log(msg: str):
    """带 flush 的打印，兼容 GitHub Actions 等 CI 环境"""
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# 统计计数器
# ---------------------------------------------------------------------------

class _CheckerStats:
    """Checker 运行期统计"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.submitted = 0       # 已提交总数
        self.completed = 0       # 已完成总数
        self.success = 0        # 可用数
        self.fail_google = 0
        self.fail_cloudflare = 0
        self.fail_openssh = 0
        self.fail_exception = 0

    @property
    def pending(self) -> int:
        with self.lock:
            return self.submitted - self.completed

    def record_ok(self) -> None:
        with self.lock:
            self.completed += 1
            self.success += 1

    def record_fail(self, step: str) -> None:
        with self.lock:
            self.completed += 1
            attr = f"fail_{step}"
            if hasattr(self, attr):
                setattr(self, attr, getattr(self, attr) + 1)

    def record_exception(self) -> None:
        with self.lock:
            self.completed += 1
            self.fail_exception += 1

    def summary(self) -> str:
        with self.lock:
            failed = self.completed - self.success
            return (
                f"[CheckerStats] "
                f"待完成={self.pending}, 已完成={self.completed}, 成功={self.success}, "
                f"失败={failed} "
                f"(google={self.fail_google}, cf={self.fail_cloudflare}, "
                f"ssh={self.fail_openssh}, exc={self.fail_exception})"
            )


def _logger_thread(stats: _CheckerStats, interval: int, stop_event: threading.Event):
    """后台 daemon 线程，定期打印统计"""
    while not stop_event.is_set():
        time.sleep(interval)
        _log(stats.summary())


# ---------------------------------------------------------------------------
# 代理验证逻辑
# ---------------------------------------------------------------------------

MAX_RETRIES = 3          # 最多重试 3 次
REQUEST_TIMEOUT = 2.5   # 每次请求超时 2.5 秒
RETRY_BASE_DELAY = 0.5   # 重试前等待基数（attempt * 0.5s）


def _check_single(proxy: Proxy) -> ValidateResult:
    """
    验证单个代理的可用性。

    验证流程（每步超时 2.5 秒，最多重试 3 次）：
    1. Google generate204 — 连通性
    2. Cloudflare          — 稳定性
    3. OpenSSH.org (TLS)  — 高安全要求网站访问能力
    """
    proxy_url = proxy.url
    proxies = {"http": proxy_url, "https": proxy_url}
    result = ValidateResult(proxy=proxy, available=False)

    # Step 1: Google generate204
    for attempt in range(MAX_RETRIES + 1):  # 0,1,2,3 共 4 次尝试
        try:
            start = time.perf_counter()
            resp = requests.get(
                "http://clients3.google.com/generate_204",
                proxies=proxies,
                timeout=REQUEST_TIMEOUT,
            )
            result.google_204_ms = round((time.perf_counter() - start) * 1000, 2)
            if 200 <= resp.status_code <= 399 or resp.status_code == 204:
                break
            result.error = f"google_204 status {resp.status_code}"
            result.fail_step = "google"
            return result
        except Exception as exc:
            result.error = repr(exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BASE_DELAY * attempt)
            else:
                result.fail_step = "google"
                return result

    # Step 2: Cloudflare
    for attempt in range(MAX_RETRIES + 1):
        try:
            start = time.perf_counter()
            resp = requests.get(
                "http://cp.cloudflare.com",
                proxies=proxies,
                timeout=REQUEST_TIMEOUT,
            )
            result.cloudflare_ms = round((time.perf_counter() - start) * 1000, 2)
            if 200 <= resp.status_code <= 399 or resp.status_code == 204:
                break
            result.error = f"cloudflare status {resp.status_code}"
            result.fail_step = "cloudflare"
            return result
        except Exception as exc:
            result.error = repr(exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BASE_DELAY * attempt)
            else:
                result.fail_step = "cloudflare"
                return result

    # Step 3: OpenSSH.org (高 TLS 要求)
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(
                "https://www.openssh.org/",
                proxies=proxies,
                timeout=REQUEST_TIMEOUT,
            )
            if 200 <= resp.status_code <= 399:
                result.openssh_ok = True
                result.available = True
                break
            else:
                result.error = f"openssh status {resp.status_code}"
                result.available = True  # openssh 返回非 2xx 只当警告，仍标记可用
                break
        except Exception as exc:
            result.error = repr(exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BASE_DELAY * attempt)
            else:
                result.fail_step = "openssh"
                return result

    return result


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def run_checker(raw_queue, result_queue, max_workers: int = 32, log_interval: int = 10):
    """
    从 raw_queue 消费 Proxy，使用进程池并发验证，写入 result_queue。
    """
    _log(f"[Checker] 启动验证服务，进程池大小: {max_workers}")

    stats = _CheckerStats()
    logger_stop = threading.Event()
    logger = threading.Thread(
        target=_logger_thread,
        args=(stats, log_interval, logger_stop),
        daemon=True,
    )
    logger.start()

    try:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures: dict = {}

            while True:
                proxy = raw_queue.get()
                if proxy is None:
                    break

                future = executor.submit(_check_single, proxy)
                futures[future] = proxy
                stats.submitted += 1

                # 及时消费已完成的任务，避免 futures 字典无限膨胀
                done = [f for f in list(futures.keys()) if f.done()]
                for f in done:
                    _consume_future(f, futures, result_queue, stats)

            # 等待剩余全部完成
            for f in as_completed(futures):
                _consume_future(f, futures, result_queue, stats)
    finally:
        logger_stop.set()
        logger.join(timeout=1)

    result_queue.put(None)
    _log(stats.summary())
    _log("[Checker] 所有代理验证完成")


def _consume_future(future, futures, result_queue, stats: _CheckerStats):
    """从 Future 取出结果写入 result_queue，更新统计"""
    proxy = futures.pop(future, None)
    if proxy is None:
        return

    try:
        res = future.result()
        result_queue.put(res)

        if res.available:
            stats.record_ok()
            _log(f"[Checker] {res.proxy.identity} OK "
                 f"(g={res.google_204_ms}ms, c={res.cloudflare_ms}ms)")
        elif res.fail_step:
            stats.record_fail(res.fail_step)
            _log(f"[Checker] {res.proxy.identity} FAIL @ {res.fail_step}")
        else:
            stats.record_exception()
            _log(f"[Checker] {res.proxy.identity} ERROR: {res.error}")

    except Exception as exc:
        from models import ValidateResult  # noqa: reimport for safety
        result_queue.put(
            ValidateResult(proxy=proxy, available=False, error=str(exc), fail_step="exception")
        )
        stats.record_exception()
        _log(f"[Checker] {proxy.identity} EXC: {exc}")