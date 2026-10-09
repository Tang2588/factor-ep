# factor-ep：EP / PE 因子计算

EP（市盈率倒数）因子的完整计算链路。样本期 2020-01-02 至 2025-12-31，共 1,455 个
交易日，统一采用「T 日因子用于 T+1 日交易」的点时间口径。

**本仓库只负责因子计算，不做因子测试。** 横截面回归、Rank IC、行业内分层回测以及
汇总文档，都在 [factor-toolkit](https://github.com/Tang2588/factor-toolkit)。

## 一、在四段流水线中的位置

整个项目是一条四段流水线，本仓库承担前三段中属于 EP 的部分：

| 阶段 | 在哪做 | 本仓库的角色 |
|---|---|---|
| ① 行情清洗 | **只在 EP 做一次** | `代码/ep_step1_样本筛选.py` —— 产出四个因子共用的行情面板 |
| ② 因子计算 | 四个因子各做一遍 | `ep_step2`–`ep_step4` —— 财报版本处理、TTM、点时间对齐，产出原始 EP |
| ③ 标准化 | 四个因子各做一遍 | `ep_step5_MAD去极值_Z标准化.py` —— 产出正式交付的标准化因子 |
| ④ 因子测试 | factor-toolkit | 不在本仓库 |

阶段 ① 只做一次的原因：股票池必须四个因子完全一致才有可比性。PB / ROE / Size 的
step1 都只是把这里产出的行情面板复制过去并校验，不重新清洗。它们的代码在
[factor-pipeline](https://github.com/Tang2588/factor-pipeline)。

## 二、快速开始

基础数据目录写死在两处，换机器需要改这两个文件：

- `代码/ep_step1_样本筛选.py` 的 `SOURCE`
- `代码/ep_step2_利润表处理.py` 的 `BASE`

```powershell
pip install -r requirements.txt

cd 代码
python ep_step1_样本筛选.py
python ep_step2_利润表处理.py
python ep_step3_TTM计算.py
python ep_step4_点时间对齐_计算PE.py
python ep_step5_MAD去极值_Z标准化.py
```

> **顺序不能变。** step1 必须最先跑，它产出的行情面板是另外三个因子的输入；
> step2–step5 严格按编号执行。

## 三、各步的输入与产出

| 步 | 脚本 | 输入 | 产出 | 行数 |
|---|---|---|---:|---:|
| 1 | `ep_step1_样本筛选.py` | 行情表 | `中间结果/_mkt_clean.parquet` | 7,081,800 |
| 2 | `ep_step2_利润表处理.py` | 利润表 | `中间结果/reports.parquet` | 266,865 |
| 3 | `ep_step3_TTM计算.py` | `reports` | `中间结果/reports_ttm.parquet` | 130,354 |
| 4 | `ep_step4_点时间对齐_计算PE.py` | 行情面板 + TTM | `中间结果/ep_raw.parquet` | 7,081,800 |
| 5 | `ep_step5_MAD去极值_Z标准化.py` | `ep_raw` | `因子结果/ep.parquet` | 7,081,800 |

step3 与 step4、step5 都需要遍历数百万行，单步实测约 2–4 分钟。

## 四、交付物

```
因子结果/ep.parquet     标准化后的 EP（正因子）
因子结果/pe.parquet     标准化后的 PE（= 1/EP，仅供展示）
```

格式是 factor-toolkit 的输入契约：

```text
index   = ['date', 'stock_code']
columns = ['signal']
```

`signal` 为经逐日横截面 MAD 去极值与 Z 标准化后的值，缺失表示当日不参与。
未经标准化的原始值保留在 `中间结果/ep_raw.parquet`、`pe_raw.parquet`。

## 五、代码分层

| 层 | 位置 | 职责 |
|---|---|---|
| 共享底层 | `代码/common/` | 分片读取 parquet；行情清洗、B 股剔除、北交所新旧代码映射 |
| 行情清洗 | `代码/ep_step1_样本筛选.py` | 股票池定义、停牌标记、北交所 242 对代码映射 |
| 财报处理 | `代码/ep_step2`、`ep_step3` | 报表版本清洗、TTM 三期拼装 |
| 因子计算 | `代码/ep_step4` | 点时间对齐、原始 EP/PE |
| 标准化 | `代码/ep_step5` | 逐日横截面 MAD 去极值与 Z 标准化 |
| 流式计算 | `代码/pure_factor_streaming.py` | 分块读写、点时间对齐、MAD+Z 的底层实现 |
| 文档转换 | `代码/md_to_latex.py` | Markdown → LaTeX |

## 六、目录说明

| 目录 | 内容 | 是否纳入 Git |
|---|---|---|
| `代码/` | 因子计算脚本与共享模块 | 是 |
| `日志/` | 变更日志与运行记录 | 是 |
| `中间结果/` | 行情面板、财报版本、TTM、原始因子 | 否（体积大，可重建） |
| `因子结果/` | 最终交付的标准化因子 | 否（体积大，可重建） |

## 七、主要文档

- `EP因子交付说明.md`：数据口径、运行步骤与交付字段
- `脚本说明.md`：逐个脚本的作用、输入、输出与依赖顺序
- `处理记录.md`：历次口径变更与完整计算过程
- `学习清单.md`：按本仓库实际用到的语法整理的 pandas / pyarrow 学习清单
- `EP_PE统计分析与因子测试计划.md` / `.tex`：统计分析、RLM 回归说明与测试计划
- `MAD去极值与标准化统计分析.md` / `.tex`：MAD 去极值与 Z 标准化专项分析
- `日志/`：共享模块抽取、回归脚本通用化等变更的完整过程

## 八、统一口径

| 项目 | 口径 |
|---|---|
| 因子方向 | `EP = TTM 归母净利润 / 总市值`，`PE = 1 / EP` |
| 样本筛选 | 不筛选 ST/PT 与次新股，只对停牌日置缺失；排除 89 只沪深 B 股 |
| 北交所代码 | 2025 年 10 月新旧代码按价格与总股本精确映射，242 对，切换日从数据推导 |
| 财报可用时间 | `VERSION_TIME = max(ACT_PUBTIME, UPDATE_TIME)` |
| 报表口径 | 合并报表（`MERGED_FLAG=1`），保留累计报告期，同一报告期保留全部版本 |
| TTM | `本期累计 + 上一年年报 − 上一年同期`，缺任一期返回缺失，不回退 |
| 时间对齐 | T 日收盘后使用截至 T 日可获得的数据；T 日因子用于 T+1 日交易 |
| 去极值与标准化 | 逐日横截面 `median ± 3 × 1.4826 × MAD` 截断后做 Z 标准化 |
