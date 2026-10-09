# -*- coding: utf-8 -*-
"""Step 1：构建沪深 A 股 + 北交所的行情面板。

不施加 ST/PT 约束；剔除沪市 B 股（900xxx.BJ）与深市 B 股
（200/201xxx.SZ）。当日交易状态与上市日期可用性保留在面板中供审计。

北交所 2025 年换代码，同一只证券在切换前后必须共享一个身份，而财报又要按
旧代码连接，因此这里同时派生两个字段：`security_id`（旧码归到新码）与
`financial_code6`（新码归到旧码）。

行情表要扫两遍：首现日与北交所切换日行情都必须全表扫完才知道，第二遍才能
用它们打标记。
"""
from pathlib import Path
import re

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from bse_code_mapping import NEW_FIRST_DATE, OLD_LAST_DATE, derive_bse_code_mapping

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "中间结果"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SOURCE = Path(r"D:\实习生学习项目\基础数据") / "chn_equ_mkt_quotation.parquet"
OUT_PATH = OUT_DIR / "_mkt_clean.parquet"
BATCH = 25_000

# 第一遍只取首现日与北交所映射需要的最小列
MAPPING_COLUMNS = [
    "date", "stock_code", "close", "pre_close",
    "close_adj", "pre_close_adj", "share_total",
]
SCAN_COLUMNS = ["date", "stock_code", "me_total", "suspended", "status"]
OUT_COLUMNS = [
    "date", "code6", "market_code", "security_id", "financial_code6",
    "bse_code_mapped", "exchange", "market_scope", "me_total", "status",
    "suspended", "suspended_unknown",
]

# B 股识别规则与预期数量：数量不符即中断，避免口径悄悄漂移
B_SHARE_RULES = [("上海 B 股", ("900",), ".BJ"), ("深圳 B 股", ("200", "201"), ".SZ")]
EXPECTED_B_SHARE_COUNTS = {"上海 B 股": 50, "深圳 B 股": 39}
MARKET_SCOPES = {"SH": "沪市 A 股", "SZ": "深市 A 股", "BJ": "北交所"}

# 时点字段可用性审计：字段是否可用、是否被用作筛选条件
FIELD_AUDIT = [
    ("ST/PT", "未提供历史字段", "不可用", 0, "不使用当前名称反推历史状态"),
    ("交易状态", "当日行情 suspended/status", "可用", 1, "只使用同一交易日行情记录，不向历史回填"),
]

CODE6_RE = re.compile(r"(\d{6})")
EXCHANGE_RE = re.compile(r"\.([A-Z]+)$")


def normalize_code(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip().str.upper()


def b_share_type(series: pd.Series) -> pd.Series:
    """逐行判断 B 股类别，非 B 股返回 NA。"""
    code = normalize_code(series)
    kind = pd.Series(pd.NA, index=code.index, dtype="string")
    for name, prefixes, suffix in B_SHARE_RULES:
        hit = code.str.startswith(prefixes, na=False) & code.str.endswith(suffix, na=False)
        kind = kind.mask(hit, name)
    return kind


source = pq.ParquetFile(SOURCE)

# ---- 第一遍：北交所切换日行情、B 股清单 ----
b_share_codes = {}
transition_rows = []
source_rows = excluded_rows = kept_rows = 0
data_start = None

for batch in source.iter_batches(columns=MAPPING_COLUMNS, batch_size=BATCH):
    chunk = batch.to_pandas()
    source_rows += len(chunk)
    chunk["date"] = pd.to_datetime(chunk["date"])
    chunk["market_code"] = normalize_code(chunk["stock_code"])
    transition_rows.append(chunk.loc[chunk["date"].isin([OLD_LAST_DATE, NEW_FIRST_DATE])])

    kind = b_share_type(chunk["stock_code"])
    is_b_share = kind.notna()
    excluded_rows += int(is_b_share.sum())
    b_share_codes.update(zip(chunk.loc[is_b_share, "market_code"], kind[is_b_share]))

    kept = chunk.loc[~is_b_share]
    if kept.empty:
        continue
    if kept["market_code"].str.extract(CODE6_RE, expand=False).isna().any():
        raise ValueError("行情表存在无法解析的股票代码")
    kept_rows += len(kept)
    batch_min = kept["date"].min()
    data_start = batch_min if data_start is None else min(data_start, batch_min)

transition = pd.concat(transition_rows, ignore_index=True)
if transition.empty:
    raise ValueError("行情表缺少北交所代码切换日期，无法生成新旧代码映射")
bse_mapping = derive_bse_code_mapping(transition)
bse_mapping.to_parquet(OUT_DIR / "bse_code_mapping.parquet", index=False)
bse_mapping.to_csv(OUT_DIR / "bse_code_mapping.csv", index=False, encoding="utf-8-sig")
old_to_new = dict(zip(bse_mapping["old_code"], bse_mapping["new_code"]))
new_to_old_code6 = dict(
    zip(bse_mapping["new_code"], bse_mapping["old_code"].str.extract(CODE6_RE, expand=False))
)

b_share_counts = pd.Series(list(b_share_codes.values())).value_counts().to_dict()
for name, expected in EXPECTED_B_SHARE_COUNTS.items():
    actual = b_share_counts.get(name, 0)
    if actual != expected:
        raise ValueError(f"行情表识别出的{name}数量为 {actual}，预期为 {expected}")

b_share_audit = pd.DataFrame({"stock_code": sorted(b_share_codes)})
b_share_audit["code6"] = b_share_audit["stock_code"].str.extract(CODE6_RE, expand=False)
b_share_audit["b_share_type"] = b_share_audit["stock_code"].map(b_share_codes)
b_share_audit.to_parquet(OUT_DIR / "b_share_codes.parquet", index=False)

# ---- 第二遍：清洗、打标记、写面板 ----
writer = None
suspended_rows = market_rows = 0

try:
    for batch in source.iter_batches(columns=SCAN_COLUMNS, batch_size=BATCH):
        mkt = batch.to_pandas()
        mkt = mkt.loc[b_share_type(mkt["stock_code"]).isna()].copy()
        if mkt.empty:
            continue
        market_rows += len(mkt)
        mkt["date"] = pd.to_datetime(mkt["date"])
        mkt["market_code"] = normalize_code(mkt["stock_code"])
        mkt["code6"] = mkt["market_code"].str.extract(CODE6_RE, expand=False)
        if mkt["code6"].isna().any():
            raise ValueError("行情表存在无法解析的股票代码")

        mkt["exchange"] = mkt["market_code"].str.extract(EXCHANGE_RE, expand=False)
        unknown = mkt.loc[~mkt["exchange"].isin(MARKET_SCOPES), "market_code"].unique()
        if len(unknown):
            raise ValueError(f"行情表存在未识别的交易所代码: {sorted(unknown)[:10]}")
        mkt["market_scope"] = mkt["exchange"].map(MARKET_SCOPES)

        mkt["security_id"] = mkt["market_code"].map(old_to_new).fillna(mkt["market_code"])
        mkt["financial_code6"] = mkt["market_code"].map(new_to_old_code6).fillna(mkt["code6"])
        mkt["bse_code_mapped"] = mkt["market_code"].isin(new_to_old_code6).astype("int8")

        if pd.MultiIndex.from_frame(mkt[["date", "code6"]]).duplicated().any():
            raise ValueError("行情表存在重复的 date + code6 记录")

        raw_suspended = pd.to_numeric(mkt["suspended"], errors="coerce")
        status = mkt["status"].astype("string").str.strip()
        mkt["suspended_unknown"] = (raw_suspended.isna() & mkt["status"].isna()).astype("int8")
        mkt["suspended"] = (
            raw_suspended.eq(1).fillna(False) | status.eq("停牌").fillna(False)
        ).astype("int8")

        out = mkt[OUT_COLUMNS]
        suspended_rows += int(out["suspended"].sum())

        table = pa.Table.from_pandas(out, preserve_index=False)
        if writer is None:
            writer = pq.ParquetWriter(OUT_PATH, table.schema)
        writer.write_table(table)
finally:
    if writer is not None:
        writer.close()

if market_rows != kept_rows:
    raise ValueError(
        f"两次行情扫描保留行数不一致: first pass={kept_rows}, second pass={market_rows}"
    )

field_audit = pd.DataFrame(
    FIELD_AUDIT,
    columns=["field", "source", "availability", "filter_applied", "point_in_time_rule"],
)
field_audit.to_csv(OUT_DIR / "point_in_time_field_audit.csv", index=False, encoding="utf-8-sig")
field_audit.to_parquet(OUT_DIR / "point_in_time_field_audit.parquet", index=False)

for line in [
    f"source rows: {source_rows:,}",
    f"B-share rows excluded: {excluded_rows:,}",
    f"B-share stocks excluded: {len(b_share_audit):,} {b_share_counts}",
    f"BSE old/new code mappings: {len(bse_mapping):,}",
    f"market rows kept: {kept_rows:,}",
    f"data start: {data_start.date()}",
    f"suspended rows (marked): {suspended_rows:,}",
    "ST/PT filter: not applied",
    "active NaN mask: suspended dates only (TTM/market-cap missingness remains natural NaN)",
    f"saved: {OUT_PATH}",
    f"saved: {OUT_DIR / 'b_share_codes.parquet'}",
    f"saved: {OUT_DIR / 'bse_code_mapping.parquet'}",
    f"saved: {OUT_DIR / 'point_in_time_field_audit.csv'}",
]:
    print(line)