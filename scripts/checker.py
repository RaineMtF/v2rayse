"""第二阶段：多线程并发验证代理可用性"""

from __future__ import annotations

import time
import threading
import queue

import httpx

from models import Proxy, ValidateResult


# ---------------------------------------------------------------------------
# 统计计数器
# ---------------------------------------------------------------------------

class _CheckerStats:
    """Checker 运行期统计"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.submitted = 0       # 已提交给线程池的代理总数
        self.completed = 0       # 已从线程池拿到结果的代理总数
        self.success = 0         # 最终可用数
        self.fail_cloudflare = 0 # 败在 cloudflare
        self.fail_openssh = 0    # 败在 openssh
        self.fail_exception = 0  # 线程内抛异常

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
                f"(cloudflare={self.fail_cloudflare}, "
                f"openssh={self.fail_openssh}, exception={self.fail_exception})"
            )


def _logger_thread(stats: _CheckerStats, interval: int, stop_event: threading.Event):
    """后台 daemon 线程，定期打印统计信息"""
    while not stop_event.is_set():
        time.sleep(interval)
        print(stats.summary())


# ---------------------------------------------------------------------------
# 代理验证逻辑（在线程内运行）
# ---------------------------------------------------------------------------

def _check_single(proxy: Proxy) -> ValidateResult:
    """
    验证单个代理的可用性。

    验证流程（不进行重试，每步严格超时 5.0 秒）：
    1. Cloudflare         — 稳定性 (严格 204 校验)
    2. OpenSSH.org (TLS)  — 高安全要求网站访问能力 (严格 200-299 校验)
    """
    proxy_url = proxy.url
    
    # 将 socks5:// 替换为 socks5h://，让代理服务器负责 DNS 解析
    if proxy_url.startswith("socks5://"):
        proxy_url = "socks5h://" + proxy_url[len("socks5://"):]
    
    proxies = {"all://": proxy_url}
    result = ValidateResult(proxy=proxy, available=False)
    TIMEOUT_SEC = 5.0

    try:
        with httpx.Client(
            proxies=proxies,
            timeout=httpx.Timeout(None, connect=TIMEOUT_SEC, read=TIMEOUT_SEC),
        ) as client:
            # Step 1: Cloudflare
            try:
                start = time.perf_counter()
                resp = client.get("https://cp.cloudflare.com")
                result.cloudflare_ms = round((time.perf_counter() - start) * 1000, 2)
                if resp.status_code != 204:
                    result.error = f"cloudflare status {resp.status_code} (Expected 204)"
                    result.fail_step = "cloudflare"
                    return result
            except Exception as exc:
                result.error = repr(exc)
                result.fail_step = "cloudflare"
                return result

            # Step 2: OpenSSH.org (高 TLS 要求)
            try:
                resp = client.get("https://www.openssh.org/")
                if 200 <= resp.status_code < 300:
                    result.available = True
                else:
                    result.error = f"openssh status {resp.status_code} (Expected 200-299)"
                    result.fail_step = "openssh"
                    return result
            except Exception as exc:
                result.error = repr(exc)
                result.fail_step = "openssh"
                return result
    except Exception as exc:
        result.error = repr(exc)
        result.fail_step = "cloudflare"
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

    # 内部任务队列，预创建固定线程池，避免 ThreadPoolExecutor 调度开销
    task_queue = queue.Queue(maxsize=max_workers * 4)
    stop_event = threading.Event()

    def _worker():
        """工作线程：从 task_queue 取任务，验证后写入 result_queue"""
        while not stop_event.is_set():
            try:
                proxy = task_queue.get(timeout=0.5)
                if proxy is None:
                    break
            except queue.Empty:
                continue

            identity = getattr(proxy, "identity", "unknown")
            print(f"[Checker] 🚀 {identity} -> 工作线程启动任务")
            result = _check_single(proxy)

            # 更新统计
            if result.available:
                stats.record_ok()
            else:
                if result.fail_step:
                    stats.record_fail(result.fail_step)
                else:
                    stats.record_exception()

            result_queue.put(result)

            identity = getattr(result.proxy, "identity", "unknown")
            status = "✅ 可用" if result.available else f"❌ 不可用 ({result.error})"
            print(f"[Checker] {identity} -> {status}")

    # 启动固定线程池
    threads = []
    for _ in range(max_workers):
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        threads.append(t)

    try:
        while True:
            proxy = raw_queue.get()
            if proxy is None:
                break
            stats.submitted += 1
            identity = getattr(proxy, "identity", "unknown")
            print(f"[Checker] ⏳ {identity} -> 已加入任务队列")
            task_queue.put(proxy)

        # 发送结束信号
        for _ in range(max_workers):
            task_queue.put(None)

        # 等待所有工作线程完成
        for t in threads:
            t.join()
    finally:
        stop_event.set()
        logger_stop.set()
        logger.join(timeout=1)

    result_queue.put(None)
    print(stats.summary())
    print("[Checker] 所有代理验证完成")
