#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
restock_rocket_monitor.py

抓取 Shopify 商品页面中 RestockRocket 插件注入的
window._RestockRocketConfig.variantsPreorderCount 字段（记为"销量"），
每次抓取到的值如果和上一次记录的不同，就追加一行到 CSV，
并自动计算本次和上一次的差额（销量变化）。

用法示例:
    python3 restock_rocket_monitor.py \
        --url "https://your-shop.com/products/xxx" \
        --variant-id 48960796590315 \
        --interval 60 \
        --csv preorder_log.csv

只抓一次（配合 GitHub Actions / cron 使用）:
    python3 restock_rocket_monitor.py --url ... --variant-id ... --csv ... --once

依赖:
    pip install requests --break-system-packages
"""

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

# 匹配形如:
#   window._RestockRocketConfig.variantsPreorderCount = {48960796590315 : parseInt("235"),};
FIELD_PATTERN = re.compile(
    r"_RestockRocketConfig\.(?P<field>\w+)\s*=\s*\{(?P<body>.*?)\}\s*;",
    re.DOTALL,
)

# 匹配对象体内的 key : value 对，value 可能是:
#   parseInt("235")   数字字符串   null   "some text"
ENTRY_PATTERN = re.compile(
    r"""
    (?P<key>\d+)\s*:\s*
    (?:
        parseInt\(\s*"(?P<num_str>-?\d+)"\s*\)   # parseInt("235")
        |
        (?P<null>null)                            # null
        |
        "(?P<str_val>[^"]*)"                      # "text"
        |
        (?P<raw_num>-?\d+)                        # 235
    )
    """,
    re.VERBOSE,
)

TARGET_FIELD = "variantsPreorderCount"
CSV_HEADER = ["timestamp_utc", "variant_id", "销量", "销量变化"]


def fetch_html(url: str, timeout: int = 15) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def parse_preorder_count(html: str, variant_id: str):
    """从 HTML 中解析出目标 variant 的 variantsPreorderCount 值（int 或 None）"""
    for m in FIELD_PATTERN.finditer(html):
        if m.group("field") != TARGET_FIELD:
            continue
        body = m.group("body")
        for em in ENTRY_PATTERN.finditer(body):
            if em.group("key") != variant_id:
                continue
            if em.group("num_str") is not None:
                return int(em.group("num_str"))
            if em.group("raw_num") is not None:
                return int(em.group("raw_num"))
            if em.group("null") is not None:
                return None
            if em.group("str_val") is not None:
                # 理论上不会出现，容错返回 None
                return None
    return None


def ensure_csv_header(csv_path: str):
    parent_dir = os.path.dirname(csv_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    if not os.path.exists(csv_path):
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)


def read_last_value(csv_path: str, variant_id: str):
    """从已有 CSV 里找这个 variant_id 最后一次记录的销量值，没有则返回 None"""
    if not os.path.exists(csv_path):
        return None
    last_value = None
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("variant_id") == str(variant_id):
                raw = row.get("销量", "")
                if raw not in ("", None):
                    try:
                        last_value = int(raw)
                    except ValueError:
                        pass
    return last_value


def append_csv_row(csv_path: str, variant_id: str, value, diff):
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                datetime.now(timezone.utc).isoformat(),
                variant_id,
                "" if value is None else value,
                "" if diff is None else diff,
            ]
        )


def monitor(url: str, variant_id: str, interval: int, csv_path: str, once: bool):
    ensure_csv_header(csv_path)
    # 每次进程启动都从 CSV 里读上一次的值，这样即使脚本是被 GitHub Actions
    # 每次全新启动一次（--once 模式），也能正确算出差额
    last_value = read_last_value(csv_path, variant_id)

    while True:
        try:
            html = fetch_html(url)
            current_value = parse_preorder_count(html, variant_id)
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] 抓取/解析失败: {e}", file=sys.stderr)
            if once:
                sys.exit(1)
            time.sleep(interval)
            continue

        if current_value != last_value:
            diff = None
            if current_value is not None and last_value is not None:
                diff = current_value - last_value
            print(
                f"[{datetime.now().isoformat()}] 销量更新: "
                f"{last_value} -> {current_value} (变化 {diff})"
            )
            append_csv_row(csv_path, variant_id, current_value, diff)
            last_value = current_value
        else:
            print(f"[{datetime.now().isoformat()}] 销量无变化，当前值 = {current_value}")

        if once:
            break
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="实时抓取 RestockRocket 销量(预售数量)数据")
    parser.add_argument("--url", required=True, help="商品页面完整 URL")
    parser.add_argument("--variant-id", required=True, help="要监控的 variant id")
    parser.add_argument("--interval", type=int, default=60, help="轮询间隔（秒），默认 60")
    parser.add_argument("--csv", default="restock_rocket_log.csv", help="CSV 输出文件路径")
    parser.add_argument("--once", action="store_true", help="只抓取一次后退出（用于测试或配合外部定时任务 cron）")
    args = parser.parse_args()

    monitor(args.url, args.variant_id, args.interval, args.csv, args.once)


if __name__ == "__main__":
    main()
