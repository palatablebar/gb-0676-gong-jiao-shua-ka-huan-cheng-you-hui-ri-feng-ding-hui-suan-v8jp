# 深度登记 公交刷卡换乘优惠与日封顶回算

规模：中型。使用者：公交清分中心复核员。核心规则：换乘按上次有效付费行程的时间窗计算，优惠与日封顶按确定顺序裁决并保留分币守恒。回执晚到时以已确认行程为权益集合重算并产出补扣退款清单。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 刷卡采信 | 查看并运行刷卡采信 | `/api/taps` | `app/modules/m1_taps/rules.py` | `tests/test_m1.py` |
| M2 线路换乘 | 查看并运行线路换乘 | `/api/routes` | `app/modules/m2_routes/rules.py` | `tests/test_m2.py` |
| M3 票价版本 | 查看并运行票价版本 | `/api/fares` | `app/modules/m3_fares/rules.py` | `tests/test_m3.py` |
| M4 行程配对 | 查看并运行行程配对 | `/api/journeys` | `app/modules/m4_journeys/rules.py` | `tests/test_m4.py` |
| M5 优惠回算 | 查看并运行优惠回算 | `/api/discounts` | `app/modules/m5_discounts/rules.py` | `tests/test_m5.py` |
| M6 清分对账 | 查看并运行清分对账 | `/api/clearing` | `app/modules/m6_clearing/rules.py` | `tests/test_m6.py` |
| M7 日结归档 | 查看并运行日结归档 | `/api/settlement` | `app/modules/m7_settlement/rules.py` | `tests/test_m7.py` |
| M8 回执对账 | 导入银行回执、裁决异常并封存 | `/receipts`，`/api/runs/<id>/receipts` | `app/modules/m8_receipts/rules.py` | `tests/test_m8.py` |

## 回执裁决

回执晚到且可拆多条。项目选择“确认制”：只有含终结回执的行程参与换乘与封顶计算，未回执行程不提前占用额度；终结回执到达后按已确认集合整体重算，逐程给出补扣、退款或不变。已否决“应享制”，因其让未落账行程提前占额。每批形成补扣退款清单，逐条回执对到具体行程，基础票价、减免、应扣、到账、补扣与退款双向守恒。回执按号幂等，重复重试不重复补扣或退款；同集合重算复用版本。重算失败保留旧版。原始刷卡与回执按sha256留档。回执未对平不封存日结。

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 旧编码 | 默认解码失败 | 回退GB18030 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_legacy_encoding_real_sample` |
| TRAP-02 | BOM | 首列名多字符 | utf-8-sig | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_bom_real_sample` |
| TRAP-03 | 带引号换行 | 拆成两条 | csv逻辑行 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_quoted_newline_is_one_record` |
| TRAP-05 | 列数不匹配 | 静默错列 | 隔离原行 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_bad_columns_real_sample` |
| TRAP-08 | 前导零ID | 编码变形 | 全程字符串 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_identifier_preserved_real_sample` |
| TRAP-14 | 重复键冲突 | 后写覆盖 | 保留首条并隔离冲突行 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_duplicate_conflict_real_sample` |
| TRAP-18 | 空态三态 | 一律成功 | 分别错误码 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_three_empty_states` |
| TRAP-26 | 旧版本提交 | 覆盖新结果 | 版本条件更新 | `app/store.py` | `tests/test_e2e.py::test_stale_review_409` |
| TRAP-06 | 全角数字 | 转换失败 | NFKC与Decimal | `app/values.py` | `tests/test_m3.py::test_thousands_and_fullwidth` |
| TRAP-07 | 金额哨兵 | 零参与扣费 | 空值隔离 | `app/values.py` | `tests/test_m3.py::test_real_sentinel_quarantined` |
| TRAP-11 | 混合偏移 | 落错版本 | UTC归整 | `app/values.py` | `tests/test_m3.py::test_timezone_equal` |
| TRAP-23 | 换乘截止点 | 重复优惠 | 截止点排除 | `app/modules/m5_discounts/rules.py` | `tests/test_m5.py::test_exact_window_excluded` |
| TRAP-25 | 票价重叠 | 随意取版 | 冲突隔离 | `app/modules/m3_fares/rules.py` | `tests/test_m3.py::test_overlap_rejected` |
| TRAP-27 | 回执晚到且拆分 | 未确认金额先占额/到达即累加 | 终结回执前不占额，拆分只累加至终结 | `app/modules/m8_receipts/rules.py` | `tests/test_m8.py::test_split_receipts_sum_until_final` |
| TRAP-28 | 重复回执 | 每收到一次就补扣/退款 | 回执号幂等，同集合复用版本 | `app/modules/m8_receipts/rules.py`、`app/store.py` | `tests/test_m8.py::test_repeated_receipt_does_not_recharge` |
| TRAP-29 | 重算失败 | 覆盖旧结果 | 先算后写，失败留痕保留旧版 | `app/modules/m8_receipts/service.py` | `tests/test_m8.py::test_recompute_failure_keeps_previous_version` |
| TRAP-30 | 未对平封存 | 直接封日结 | 未对平返回409并禁封 | `app/store.py` | `tests/test_e2e_receipts.py::test_seal_blocked_with_pending` |
| TRAP-31 | 原始记录丢失 | 只留解析结果 | 刷卡与回执原文按sha256留档可下载 | `app/store.py` | `tests/test_e2e_receipts.py::test_raw_documents_export` |

## 对账规则

输入原始逻辑行=最终采纳来源行+隔离来源行+重复行+空白行+分段表头。派生记录用_sources保存完整来源，因此分组不丢行。业务预期合计与上游申报合计之差等于逐行差异之和。结果按输入摘要、规则版本及参数产生唯一批次，事务写入；同源重跑只复用已有批次。

回执对账按行程逐条对账：已到账合计+补扣合计-退款合计=应扣合计，基础票价合计=应扣+换乘减免+封顶减免。回执集合摘要由回执号、行程、金额、到达时刻、终结标记、人工裁决与参数唯一确定，同摘要复用版本；写入前完成全部计算，失败只记错误不覆盖旧版。封存读取最新对账版本，未对平即拒绝。

## 已知不覆盖

4MB以上文件需预先按业务键分批；不模拟生产支付与外部回调。人工复核只变更批次状态，不改写原始记录。
