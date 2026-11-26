# FaultReplay Agent v0.1

FaultReplay Agent 反复回放事件日志，缩减出仍能触发同一断言的输入序列，并导出 pytest 回归测试。

[English](README.md) · MIT

```sh
python -m pip install -e ".[test]"
faultreplay demo
```

需要 Python 3.11 或更新版本。安装完成后，演示和测试均可离线运行，无需 API Key。

```text
Original log: 15 records (13 replay inputs)
Target: corridor.turn_on_once | light.turn_on [0, 500) ms <= 1
Status: complete; replays: 18/100
Report: faultreplay-output/report.md
Reduced inputs: 3 (motion-01, motion-02, motion-03)
Observed light.turn_on at [50, 250] ms (original) and [50, 250] ms (reduced)
Single-deletion verified: yes
Verify exported regression: python -m pytest -q "faultreplay-output/test_regression.py"
Reproduce on failing simulator: python -m pytest -q "faultreplay-output/test_regression.py" -p faultreplay.pytest_plugin --faultreplay-simulator faultreplay.corridor:BUGGY
```

## 验证走廊灯回归测试

导出的 `test_regression.py` 用相同的断言分别测试 `original.jsonl` 和 `reduced.jsonl`。断言规定在 `[0, 500)` 毫秒内最多发出一次 `light.turn_on`。

```sh
python -m pytest -q "faultreplay-output/test_regression.py"
python -m pytest -q "faultreplay-output/test_regression.py" -p faultreplay.pytest_plugin --faultreplay-simulator faultreplay.corridor:BUGGY
```

| 模拟器 | 原始日志与缩减日志的开灯时间 | pytest 实际结果 |
| --- | --- | --- |
| 修复版（导出测试的默认值） | 均为 50 ms | `2 passed` |
| 故障版 | 均为 50、250 ms | `2 failed`，两例均违反 `corridor.turn_on_once` |

将生成的测试移入项目时，把 `original.jsonl`、`reduced.jsonl`、`initial_state.json` 和 `assertions.json` 一起复制到测试文件所在目录，并安装 FaultReplay Agent 和相应的模拟器适配器。源示例位于 `src/faultreplay/examples/corridor/`。

## 接入自己的事件日志

JSONL 每行包含唯一的 `id`、非负整数 `offset_ms`、事件 `type` 和对象 `payload`。按 `offset_ms` 升序写入日志，同一时间戳的事件按文件顺序执行。`direction` 默认为 `input`。用 `"direction":"output"` 标记历史输出，供报告展示。每轮回放从初始状态的副本和 0 毫秒开始，按事件的原始偏移更新虚拟时钟并注入输入事件。

```jsonl
{"id":"motion-01","offset_ms":50,"type":"sensor.motion","payload":{"active":true}}
{"id":"history-on-01","offset_ms":50,"type":"light.turn_on","payload":{"light":"corridor"},"direction":"output"}
```

初始状态是 JSON 对象，例如 `{"motion": false}`。断言文件包含一个或多个计数断言，并用 `target_assertion_id` 选定需要复现的故障。`window_ms` 包含起点，排除终点。

```json
{
  "target_assertion_id": "corridor.turn_on_once",
  "assertions": [
    {"id": "corridor.turn_on_once", "output_type": "light.turn_on", "window_ms": [0, 500], "max_count": 1}
  ]
}
```

在可导入的 Python 模块中定义 `faultreplay.replay.Adapter` 实例。`Adapter` 的 `create(initial_state)` 每轮创建一个模拟器。模拟器实现 `handle(event, clock)`，用 `clock.now_ms` 读取当前虚拟时间，用 `clock.emit(output_type, payload)` 发出当前时刻的输出。可选的 `validate_initial` 和 `validate_event` 在首次回放前校验模拟器的输入。

```sh
faultreplay reduce --events events.jsonl --initial-state initial_state.json --assertions assertions.json --simulator my_app.replay:BUGGY --regression-simulator my_app.replay:FIXED --max-replays 100 --output replay-result
```

`--regression-simulator` 指定导出测试默认使用的适配器，省略时沿用 `--simulator`。CLI 退出码 `0` 表示导出了回归测试，`1` 表示本次运行只导出诊断报告，`2` 表示参数、输入或写入错误。

## 模型与回放预算

添加 `--model my_app.model:suggest` 可启用模型建议。`suggest(system_prompt, message_json)` 接收运行时提示词，以及目标断言、当前事件的 ID 与摘要、当前观测和最近的回放结果。它返回形如 `{"remove_ids":["temp-01","temp-02"]}` 的 JSON 文本。程序校验建议并自行执行回放。模型加载失败、调用异常或建议无效时，使用确定性策略依次尝试事件类别、连续区段和逐条删除。

`--max-replays` 默认是 100，包含基线、候选和最终复核的实际运行次数。基线和每个候选各运行两次，并比较输出事件的时间、类型与内容。候选必须正常结束并稳定违反目标断言。异常或观测不一致的候选记为无法判定。基线无法稳定复现时，报告原始观测并结束。每轮删减前预留两次回放，用于复核最终选中的序列。

预算不足时，`report.md` 列出已验证的最短序列和尚待检查的删除批次。报告还会标出回放无法判定的批次。只有对当前序列的每一种单事件删除都得到稳定结果且目标断言均未失败，报告才会显示 `Single-deletion verified: yes`（逐条删除已验证）。

## 开发验证

```sh
python -m pytest -q
faultreplay demo --max-replays 8 --output faultreplay-output/budget-8
```

GitHub Actions 在 Python 3.11 和 3.12 上运行测试、离线演示和生成的回归测试。测试套件用模拟模型验证建议、回放反馈和离线回退，也验证导出的测试会在故障版和修复版上分别得到预期结果。

## 许可

[MIT](LICENSE)
