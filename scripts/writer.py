"""第三阶段：收集并写入验证通过的代理"""

from __future__ import annotations

import os
from typing import Dict, List

from models import ValidateResult


def run_writer(result_queue, base_dir: str, freeproxy_list: list):
    """
    从 result_queue 消费验证结果，按分类写入 configs/ 目录。

    - 根据 config.yml 中每个分类的 ``file`` 喜欢他名，默认 ``{category}.txt``
    - 全部完成后退出
    """
    # category -> list[ValidateResult]
    groups: Dict[str, List[ValidateResult]] = {}
    total_valid = 0
    total_invalid = 0

    while True:
        result: ValidateResult = result_queue.get()
        if result is None:
            break

        if result.available:
            total_valid += 1
            cat = result.proxy.category
            groups.setdefault(cat, []).append(result)
        else:
            total_invalid += 1

    # 写入文件
    configs_dir = os.path.join(base_dir, "configs")
    os.makedirs(configs_dir, exist_ok=True)

    # 构建 category -> file_name 映射
    cat_to_filename: Dict[str, str] = {}
    for fp in freeproxy_list:
        if not isinstance(fp, dict):
            continue
        name = list(fp.keys())[0]
        config = fp[name]
        file_name = config.get("file", f"{name}.txt")
        cat_to_filename[name] = file_name

    for cat, results in groups.items():
        file_name = cat_to_filename.get(cat, f"{cat}.txt")
        file_path = os.path.join(configs_dir, file_name)

        with open(file_path, "w", encoding="utf-8") as f:
            for r in results:
                f.write(r.proxy.raw_str + "\n")

        print(f"[Writer] 写入 {file_path}: {len(results)} 个可用代理")

    print(f"[Writer] 总计: {total_valid} 可用, {total_invalid} 不可用")
    print("[Writer] 全部完成")
