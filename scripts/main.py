"""入口模块：三阶段多进程管道"""

from __future__ import annotations

import os
import shutil
import multiprocessing

import yaml

from fetcher import run_fetcher
from checker import run_checker
from writer import run_writer


def merge_files(merge_list, base_dir):
    """按配置合并文件（兼容旧逻辑）"""
    if not merge_list:
        return

    configs_dir = os.path.join(base_dir, "configs")
    print(f"[Merge] 开始合并文件于 {configs_dir}")

    for entry in merge_list:
        if not isinstance(entry, dict):
            continue

        for target_file, source_files in entry.items():
            target_path = os.path.join(configs_dir, target_file)
            print(f"[Merge] 正在合并 {source_files} -> {target_file}")

            unique_lines = []
            seen = set()
            for src in source_files:
                src_path = os.path.join(configs_dir, src)
                if os.path.exists(src_path):
                    with open(src_path, "r", encoding="utf-8") as f:
                        for line in f:
                            clean_line = line.strip()
                            if clean_line and clean_line not in seen:
                                unique_lines.append(clean_line)
                                seen.add(clean_line)
                else:
                    print(f"[Merge] 警告: 源文件 {src} 不存在")

            if unique_lines:
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(unique_lines) + "\n")
                print(
                    f"[Merge] 成功创建 {target_file}（共 {len(unique_lines)} 行）"
                )
            else:
                print(f"[Merge] 跳过 {target_file}：无内容")


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 清理输出目录
    configs_dir = os.path.join(base_dir, "configs")
    if os.path.exists(configs_dir):
        print(f"[Main] 清理输出目录: {configs_dir}")
        shutil.rmtree(configs_dir)
    os.makedirs(configs_dir, exist_ok=True)

    # 读取配置
    config_path = os.path.join(base_dir, "config.yml")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    freeproxy_list = config.get("freeproxy_list", [])
    merge_list = config.get("merge_list", [])

    print(f"[Main] 启动 Pipeline: {len(freeproxy_list)} 个分类")

    # 创建跨进程队列
    raw_queue = multiprocessing.Queue()
    result_queue = multiprocessing.Queue()

    # 三个独立进程
    fetcher_proc = multiprocessing.Process(
        target=run_fetcher,
        args=(freeproxy_list, raw_queue),
        name="Fetcher",
    )
    checker_proc = multiprocessing.Process(
        target=run_checker,
        args=(raw_queue, result_queue),
        name="Checker",
    )
    writer_proc = multiprocessing.Process(
        target=run_writer,
        args=(result_queue, base_dir, freeproxy_list),
        name="Writer",
    )

    # 启动
    fetcher_proc.start()
    checker_proc.start()
    writer_proc.start()

    # 等待完成
    fetcher_proc.join()
    print("[Main] Fetcher 已完成")

    checker_proc.join()
    print("[Main] Checker 已完成")

    writer_proc.join()
    print("[Main] Writer 已完成")

    # 合并文件
    merge_files(merge_list, base_dir)

    print("[Main] 全部完成")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
