# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

## 回执

- `GET /api/receipts`：回执列、模板与权益口径。
- `POST /api/runs/<id>/receipts`：导入回执并重算。可传multipart `file`、JSON `csv` 文本或 JSON `sample`（receipts_partial.csv、receipts_full.csv、receipts_late.csv、receipts_conflict.csv）。返回新版本、逐程清单、入账统计与`intake`。重复收到同一回执只计重复，不产生新的补扣或退款。
- `GET /api/runs/<id>/receipts`：已入账回执、冲突回执、人工裁决、原始留档、封存状态与最新对账。
- `GET /api/runs/<id>/recon`：最新对账版本；`/recon/versions`另含失败记录。
- `POST /api/runs/<id>/receipts/<receipt_id>/resolve`：人工裁决异常回执，body为`{"decision":"已核对"|"忽略","note":"..."}`，裁决后立即重算。裁决不删除回执原文。
- `POST /api/runs/<id>/seal`：封存日结。最新对账未对平返回409 `UNBALANCED_RECEIPTS`；已封存重复调用返回同一封存结果。
- `GET /api/runs/<id>/seal`：封存状态。
- `GET /api/runs/<id>/raw/<sha256>`：下载原始刷卡或回执文件。

回执对账失败（如守恒不成立）返回422，错误写入失败记录，最新成功版本保持不变。已封存批次拒绝新回执、重算与复核，返回409 `SEALED`。
