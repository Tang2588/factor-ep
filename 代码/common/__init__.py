# -*- coding: utf-8 -*-
"""EP 因子项目的共享底层模块。

本包只提供因子计算链用到的数据准备函数，不执行业务阶段逻辑，也不写出结果文件。

- ``parquet_io`` 分片读取 parquet 面板
- ``market``     行情快照清洗、B 股剔除、北交所新旧代码映射

原来还有 ``calendar``、``returns``、``panel``、``paths`` 四个模块，它们只服务于
已经移出本仓库的测试脚本（横截面回归、Rank IC、分层回测）。那些功能现在归
factor-toolkit（因子测试工具箱），因此一并删除；本仓库只保留因子计算需要的部分。
"""
from __future__ import annotations

from .market import (
    apply_security_id,
    b_share_mask,
    derive_bse_mapping,
    extract_code6,
    normalize_market_code,
    prepare_market_snapshot,
)
from .parquet_io import (
    MarketIndex,
    read_all_dates,
    read_selected_dates,
    scan_market_index,
)

__all__ = [
    "MarketIndex",
    "apply_security_id",
    "b_share_mask",
    "derive_bse_mapping",
    "extract_code6",
    "normalize_market_code",
    "prepare_market_snapshot",
    "read_all_dates",
    "read_selected_dates",
    "scan_market_index",
]
