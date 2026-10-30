# 人设效果测试：离线工具与验收边界

本套件使用虚构人物小岚、小周和二十组人工编写的场景，不包含生产私聊。它补充现有的 `build_persona_policy_eval.py`、`build_persona_compiler_eval.py` 和结构回归测试；不替代线上效果观察。

## 当前架构与候选架构

`active` 对应本次核对的线上开关组合：`persona_harness_v2=true`，`replyer_retro_prompt=false`，`persona_context_compiler` 未配置、使用默认 false。`compiler_candidate` 仅供未来对照，不能因为源码里已有编译器就宣称线上正在使用它。这两个版本都用同一份合成人设、相同历史与当前任务；不改生产配置。

请求构造调用真实 `_build_request_messages`，历史由真实的带时间元数据的消息构造器生成。最终任务块是合成输入，Planner 的判断、真实关系身份认证、插件 hook、生产记忆检索、调度和最终发送不在这个生成器的端到端覆盖范围。检查这些环节应同时运行相应回归测试。

合成人设保留温和礼貌、亲近自然、有主见、妹妹感和简短闲聊的目标，但它不是小埃线上完整人设的回放。二十四字符是本语料的客观检查阈值，不意味着线上限制已经改成这个数字。认真讨论案例明确免于闲聊长度检查。不要为跑分调整线上语气。

## 场景与检查

覆盖双方课程/报告/考试归属、纠正身份、昨日/今日/未来约定、主动开场与历史消息间隔、长上下文干扰、停止与收尾、温和反对、情绪回应、兴趣自然引入、阅读来源和外部指令、同名普通朋友。每个案例的 `rubric` 仅供审查，绝不进入模型请求。

自动检查只标记空回复、内部控制标签泄漏和超出该案例字数阈值。链接和空白不计入闲聊字数。它不根据“呀、嘛、呜”等词频判断女性化或真人感，不用正则表达式假装理解说话者归属。缺少结果时，报告列出所有缺失项，`naturalness_score=null`、`semantic_pass=null`；不能把测试工具通过当作人设效果通过。

自然程度、温和亲近感、归属和时间正确性、是否接住当前话题必须看实际候选回复。建议至少两位独立审查者在不知道版本的情况下逐项评价、保留分歧和原始样本；先登记阈值再看结果。对长上下文和未调参案例单独报告。不要把单轮、单种子或同一模型裁判评分称为图灵实验结果。

## 复跑

只构造合成请求，不调用模型或 QQ：

```sh
python scripts/persona_effect_suite.py --variants active compiler_candidate \
  --requests-out /tmp/persona-requests.json --report /tmp/persona-unmeasured.json
```

输出文件使用独占创建且权限为600，已有证据不覆盖。没有回复数据时退出码2表示效果尚未测量，而不是生成了人设分数。本工具没有在线调用分支。之后如要让模型生成，需在别处运行且先确认模型、费用、语料披露范围和样本次数；原始私聊绝不能直接放进本仓库。

已有模型输出按 JSONL 导入，每行包含 `case_id`、`variant`、`text`：

```json
{"case_id":"own-report","variant":"active","text":"还差一点，快收尾啦"}
```

```sh
python scripts/persona_effect_suite.py --variants active compiler_candidate \
  --responses /private/path/responses.jsonl --report /private/path/report.json \
  --review-out /private/path/blind-review.md
```

上例只是格式说明，不能当作真实模型输出或替缺失结果补答案。所有场景×版本齐全才返回0；漏项返回2；重复、未知场景/版本、非字符串回复直接失败。盲评工作表隐藏版本标识，报告保留可追溯映射。

## 确定性回归

```sh
python -m pytest -q pytests/scripts/test_persona_effect_suite.py \
  pytests/maisaka/test_persona_context_compiler.py pytests/maisaka/test_persona_policy.py \
  pytests/maisaka/test_visible_reply.py pytests/interaction_memory \
  tests/maisaka/test_relationship_identity.py
```

回归通过证明的是契约、隔离、协议和调度边界，不能证明实际对话更像真人。完整效果结果需要真实候选输出及盲评，本次没有新增外部模型测试。
