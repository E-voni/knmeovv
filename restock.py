#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
restock_rocket_monitor.py

实时抓取 Shopify 商品页面中 RestockRocket 插件注入的
window._RestockRocketConfig 数据（预售数量、库存、预售上限等），
按固定间隔轮询，打印变化并追加写入 CSV。

用法示例:
    python3 restock_rocket_monitor.py \
        --url "https://your-shop.com/products/xxx" \
        --variant-id 48960796590315 \
        --interval 60 \
        --csv preorder_log.csv

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
# 的赋值语句，字段名可变（variantsPreorderCount / variantsInventoryQuantity / ...）
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

FIELDS_OF_INTEREST = [
    "variantsInventoryPolicy",
    "variantsInventoryQuantity",
    "variantsPreorderCount",
    "variantsPreorderCountForMarket",
    "variantsPreorderMaxCount",
    "variantsPreorderMaxCountForMarket",
    "variantsShippingText",
    "variantsShippingTextForMarket",
]


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


def parse_config(html: str) -> dict:
    """解析出 { field_name: { variant_id(str): value, ... }, ... }"""
    result = {}
    for m in FIELD_PATTERN.finditer(html):
        field = m.group("field")
        if field not in FIELDS_OF_INTEREST:
            continue
        body = m.group("body")
        entries = {}
        for em in ENTRY_PATTERN.finditer(body):
            key = em.group("key")
            if em.group("num_str") is not None:
                entries[key] = int(em.group("num_str"))
            elif em.group("null") is not None:
                entries[key] = None
            elif em.group("str_val") is not None:
                entries[key] = em.group("str_val")
            elif em.group("raw_num") is not None:
                entries[key] = int(em.group("raw_num"))
        result[field] = entries
    return result


def extract_variant_snapshot(config: dict, variant_id: str) -> dict:
    snapshot = {}
    for field in FIELDS_OF_INTEREST:
        entries = config.get(field, {})
        snapshot[field] = entries.get(variant_id)
    return snapshot


def ensure_csv_header(csv_path: str):
    if not os.path.exists(csv_path):
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp_utc", "variant_id"] + FIELDS_OF_INTEREST)


def append_csv_row(csv_path: str, variant_id: str, snapshot: dict):
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [datetime.now(timezone.utc).isoformat(), variant_id]
            + [snapshot.get(field) for field in FIELDS_OF_INTEREST]
        )


def format_snapshot(snapshot: dict) -> str:
    return " | ".join(f"{k}={v}" for k, v in snapshot.items())


def monitor(url: str, variant_id: str, interval: int, csv_path: str, once: bool):
    ensure_csv_header(csv_path)
    last_snapshot = None

    while True:
        try:
            html = fetch_html(url)
            config = parse_config(html)
            snapshot = extract_variant_snapshot(config, variant_id)
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] 抓取/解析失败: {e}", file=sys.stderr)
            if once:
                sys.exit(1)
            time.sleep(interval)
            continue

        if snapshot != last_snapshot:
            print(f"[{datetime.now().isoformat()}] 数据变化 -> {format_snapshot(snapshot)}")
            append_csv_row(csv_path, variant_id, snapshot)
            last_snapshot = snapshot
        else:
            print(f"[{datetime.now().isoformat()}] 无变化 -> {format_snapshot(snapshot)}")

        if once:
            break
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="实时抓取 RestockRocket 预售/库存数据")
    parser.add_argument("--url", required=True, help="商品页面完整 URL")
    parser.add_argument("--variant-id", required=True, help="要监控的 variant id")
    parser.add_argument("--interval", type=int, default=60, help="轮询间隔（秒），默认 60")
    parser.add_argument("--csv", default="restock_rocket_log.csv", help="CSV 输出文件路径")
    parser.add_argument("--once", action="store_true", help="只抓取一次后退出（用于测试或配合外部定时任务 cron）")
    args = parser.parse_args()

    monitor(args.url, args.variant_id, args.interval, args.csv, args.once)


if __name__ == "__main__":
    main()
