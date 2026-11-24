# 跨轮次伦理题库泄漏校正与题组轮换

历届伦理前后测、同源情境和新一轮题组流转的合成资料。数据不含真实学生、患者或就诊记录。
数据表在 `data/`，业务字段和交换约定见 [资料口径](data-contract.md)。

## 问题与方法概述

教研组要回答两类问题，且不能用一个"校正总分"掩盖证据缺口：

1. **测量比较**：在讲评材料可能泄漏到部分学生的情况下，比较本届前后变化与届间差异，
   并把"评分版次变化"与"练过材料带来的提高"分开。
2. **组卷与轮换**：在情境类别、时长、开封场次、批改容量约束下同时组出若干题组，
   保证机会可比、合法锚题支持跨组链接，并在新重合证据出现时复查未开封题组。

核心口径（实现见 `ethics_assess/exposure.py`）：

- 接触判定按**同源情境（root）**而非同题号；只删同题号会漏掉练过改写版的学生。
- 判定必须结合**该次出件时间与收件范围**：`delivered` 且早于开封才确认接触，
  `uncertain` 归未知档（不进主分析、只做敏感性边界），`returned` 不构成接触。
- `overlap` 的 confirmed 边参与同源闭包，candidate 边只产生未知档且不传递；
  证据收件晚于场次开封的，不回溯适用于当时口径，但触发对未开封题组的复查。
- 接触同源情境与使用共同评分标准是两个正交维度：`old_score` 题永不与 `common` 题合并。
- 只在独立证据仍够的层（共同量表 + 干净题 + 有效作答）给估计；
  样本不足或无共同题的层明确标 `不可估计` 并列出限制。

## 目录结构

```
ethics_assess/
  bundle.py       资料包只读装载与口径校验（表头、词表、引用、时间线、分数范围）
  exposure.py     材料接触三档判定（干净/确认接触/未知接触）与证据链
  attendance.py   重复件、缺考、缺交、未知收件、整场无记录的分别留痕
  comparison.py   分层前后增益、届间 Welch 差异、练习效应 DiD、评分版次分列
  assembly.py     组卷硬约束、跨组锚题链接、题库消耗与候选重合的或有损失
  review.py       出件后新证据复查、已开考成绩可支持说法、替代卷双轨公示
  cli.py          机器可读 JSON 命令行
  report.py       教研组可读的中文 Markdown 报告
config/           受众映射、组卷方案、疑问映射、新证据等教务配置
tests/            29 个单元测试（含内存最小资料包夹具）
```

## 使用

统计环境用 `requirements.txt` 锁定的版本。

```sh
python3 -m pip install -r requirements.txt

# 1. 结构校验（只读，不符合口径即退出）
python3 inspect_bundle.py data
python3 -m ethics_assess.cli data validate

# 2. 某场次的材料接触分档
python3 -m ethics_assess.cli data exposure F32-post

# 3. 第 3 届前后变化、练习效应，并与第 2 届做届间差异
python3 -m ethics_assess.cli data compare 3 --other-term 2

# 4. 组卷方案合法性与跨组链接评估
python3 -m ethics_assess.cli data assemble config/plan_replacement.json \
    --audience-map config/audience_map.json --questions config/questions.json

# 5. 新重合证据到达后的复查（含替代卷双轨公示口径）
python3 -m ethics_assess.cli data review config/new_edges.json \
    --as-of 2026-10-13T18:00:00+08:00 \
    --audience-map config/audience_map.json \
    --replacements config/plan_replacement.json

# 6. 汇总成中文报告
python3 -m ethics_assess.report data --term 3 --other-term 2 \
    --audience-map config/audience_map.json \
    --actual-plan config/plan_actual.json \
    --replacement-plan config/plan_replacement.json \
    --new-edges config/new_edges.json --review-as-of 2026-10-13T18:00:00+08:00 \
    --output report.md

# 测试
python3 -m unittest discover -s tests -v
```

## 输出如何对应业务结论

- **数据质量台账**：重复件剔除、确认未参加、到期缺交、未知收件、整场无记录分别计数，
  各自对比较样本的影响不同，不合并成单一"缺考"。
- **接触审计**：每场每题给出干净/确认/未知三档人数，未知档只进敏感性分析。
- **分层增益与届间差异**：按题类×分项×接触档分别报告样本量、点估计、95% 置信区间、
  效应量与限制；没有共同干净题的题类直接标"不可识别"。
- **练习效应 DiD**：处理班（材料接触）与对照班（干净）在接触题相对干净题上的额外增益，
  平行趋势、无溢出等假设随结果列出；置信区间跨 0 时不断言存在练习效应。
- **组卷违规码**：锚题历史不足、受众接触、同 root 独立场景预算、机会已消耗、
  批改容量、无共同锚题等逐卷列出；无共同合法锚题的两套卷明确不共享可比尺度。
- **复查与双轨公示**：已开考成绩保留但受限说法，封存卷开封前必须处置；
  替代卷另出"旧成绩公示依据"与"当前可比性说明"两份并行文本。

资料检查入口只读；所有分析命令不写回 `data/`。配置文件中的 `_comment` 字段会被自动忽略。
