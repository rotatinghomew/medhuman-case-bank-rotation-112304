# 资料口径

`root` 标记源情境，`overlap` 还可确认原先不同源的题实际重叠；未证实的关系列为候选，不能作为同源已知证据。期次、班组和人员均为合成。单题有两个分项，各为 0 到 10。评分版次影响能否连接前后，认作链接的评阅由共同量表字段及独立开封记录共同支持。未收到、未知送达、明确缺考的区别分别由出件与考务状态记录；前后两次必须分别匹配到场次。下一轮的封存同轮共享限额只适用指定的锚题，非锚题共享同源情境的题目不能同时作为独立可用题。

数据表使用 UTF-8 CSV，第一行是字段名。同一业务号的引用指数据字典约定的原始对象；空白表示来源未提供，集合字段由逗号分隔后整体按 CSV 转义。
- `task.csv` 的字段为 `id`、`root`、`category`、`minutes`、`scale`、`status`。
- `student.csv` 的字段为 `id`、`term`、`group`、`background`。
- `use.csv` 的字段为 `form`、`term`、`group`、`stage`、`task`、`opened`、`state`。
- `answer.csv` 的字段为 `student`、`form`、`task`、`skill`、`score`、`received`、`status`。
- `lesson_issue.csv` 的字段为 `id`、`root`、`term`、`groups`、`sent`、`state`。
- `overlap.csv` 的字段为 `a`、`b`、`status`、`received`。
- `next_form.csv` 的字段为 `form`、`audience`、`opens`、`state`、`min_categories`、`minutes`、`anchor_capacity`、`graders`。
- `seal.csv` 的字段为 `form`、`task`、`status`、`time`。
- `anchor.csv` 的字段为 `task`、`scope`、`min_independent_uses`、`max_forms`。
- `team.csv` 的字段为 `grader`、`available_day`、`skill`、`capacity`。
