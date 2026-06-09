"""第二阶段：多进程并发验证代理可用性"""

from __future__ import annotations

import time
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed

import requests

from models import Proxy, ValidateResult


# ---------------------------------------------------------------------------
# 统计计数器
# ---------------------------------------------------------------------------

class _CheckerStats:
    """Checker 运行期统计"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.submitted = 0       # 已提交给进程池的代理总数
        self.completed = 0       # 已从进程池拿到结果的代理总数
        self.success = 0         # 最终可用数
        self.fail_google = 0     # 败在 google generate204
        self.fail_cloudflare = 0 # 败在 cloudflare
        self.fail_openssh = 0    # 败在 openssh
        self.fail_exception = 0  # 进程内抛异常

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
                f"(google={self.fail_google}, cloudflare={self.fail_cloudflare}, "
                f"openssh={self.fail_openssh}, exception={self.fail_exception})"
            )


def _logger_thread(stats: _CheckerStats, interval: int, stop_event: threading.Event):
    """后台 daemon 线程，定期打印统计信息"""
    while not stop_event.is_set():
        time.sleep(interval)
        print(stats.summary())


# ---------------------------------------------------------------------------
# 代理验证逻辑
# ---------------------------------------------------------------------------

def _check_single(proxy: Proxy) -> ValidateResult:
    """
    验证单个代理的可用性（含 5 次重试）。

    验证流程（每步超时 5 秒）：
    1. Google generate204 — 连通性
    2. Cloudflare          — 稳定性
    3. OpenSSH.org (TLS)  — 高安全要求网站访问能力
    """
    proxy_url = proxy.url

    result = ValidateResult(proxy=proxy, available=False)

    # Step 1: Google generate204
    for attempt in range(2):
        try:
            start = time.perf_counter()
            resp = requests.get(
                "http://clients3.google.com/generate_204",
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=2.5,
            )
            google_time = round((time.perf_counter() - start) * 1000, 2)
            if 200 <= resp.status_code <= 399 or resp.status_code == 204:
                result.google_204_ms = google_time
                break
            else:
                result.error = f"google_204 status {resp.status_code}"
                result.fail_step = "google"
                return result
        except Exception as exc:
            result.error = repr(exc)
            if attempt < 4:
                time.sleep(1 + attempt)
            else:
                result.fail_step = "google"
                return result

    # Step 2: Cloudflare
    for attempt in range(2):
        try:
            start = time.perf_counter()
            resp = requests.get(
                "http://cp.cloudflare.com",
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=2.5,
            )
            cf_time = round((time.perf_counter() - start) * 1000, 2)
            if 200 <= resp.status_code <= 399 or resp.status_code == 204:
                result.cloudflare_ms = cf_time
                break
            else:
                result.error = f"cloudflare status {resp.status_code}"
                result.fail_step = "cloudflare"
                return result
        except Exception as exc:
            result.error = repr(exc)
            if attempt < 4:
                time.sleep(1 + attempt)
            else:
                result.fail_step = "cloudflare"
                return result

    # Step 3: OpenSSH.org (高 TLS 要求)
    for attempt in range(2):
        try:
            resp = requests.get(
                "https://www.openssh.org/",
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=2.5,
            )
            if 200 <= resp.status_code <= 399:
                result.openssh_ok = True
                result.available = True
                break
            else:
                result.error = f"openssh status {resp.status_code}"
                result.available = True
                break
        except Exception as exc:
            result.error = repr(exc)
            if attempt < 4:
                time.sleep(1 + attempt)
            else:
                result.fail_step = "openssh"
                return result

    return result


# ---------------------------------------------------------------------------
# 供 multiprocessing.Process 进程内直接调用的入口
# ---------------------------------------------------------------------------

def run_checker(raw_queue, result_queue, max_workers: int = 16, log_interval: int = 30):
    """
    从 raw_queue 消费 Proxy 对象，使用 16 进程池并发验证，
    将 ValidateResult 写入 result_queue，完成后发送哨兵 None。
    """
    print(f"[Checker] 启动验证服务，进程池大小: {max_workers}")

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

                # 消费已完成的任务，避免内存无限膨胀
                done = [f for f in list(futures.keys()) if f.done()]
                for f in done:
                    _consume_future(f, futures, result_queue, stats)

            # 等待剩余全部完成
            for f in as_completed(futures):
                _consume_future(f, futures, result_queue, stats)
    finally:
        logger_stop.set()
        logger.join(timeout=1)

    # 发送完成哨兵
    result_queue.put(None)
    print(stats.summary())
    print("[Checker] 所有代理验证完成")


def _consume_future(future, futures, result_queue, stats: _CheckerStats):
    """安全地从 Future 取出结果并写入 result_queue，处理异常"""
    proxy = futures.pop(future, None)
    if proxy is None:
        return

    try:
        res = future.result()
        result_queue.put(res)
        status = "可用" if res.available else f"不可用 ({res.error})"
        print(f"[Checker] {res.proxy.identity} -> {status}")

        if res.available:
            stats.record_ok()
        elif res.fail_step:
            stats.record_fail(res.fail_step)
        else:
            # 理论上不该走到这里，兜底
            stats.record_exception()
    except Exception as exc:
        from models import ValidateResult  # noqa: reimport for safety
        result_queue.put(
            ValidateResult(proxy=proxy, available=False, error=str(exc), fail_step="exception")
        )
        stats.record_exception()
        print(f"[Checker] {proxy.identity} -> 异常: {exc}")
