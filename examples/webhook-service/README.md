# Replay a duplicate webhook delivery

Copy this directory into a standalone project and run the following from the copy. Install FaultReplay Agent with its `test` extra in the same Python environment first. For a local clone, use `python -m pip install -e "/path/to/faultreplay-agent[test]"`.

```sh
python -m faultreplay reduce --events events.jsonl --initial-state state.json --assertions assertions.json --simulator webhook_adapter:BUGGY --regression-simulator webhook_adapter:FIXED --output replay-result
python -m pytest -q replay-result/test_regression.py
python -m pytest -q replay-result/test_regression.py -p faultreplay.pytest_plugin --faultreplay-simulator webhook_adapter:BUGGY
```

The five captured deliveries reduce to `delivery` and `retry`. Both enqueue the same invoice in the buggy implementation. The fixed implementation enqueues it once, so both generated tests pass. Both fail against the buggy implementation on `webhook.enqueue_once`.

`service.py` is a synthetic application module with no FaultReplay dependency. The adapter creates a fresh service for each replay, maps a recorded input to the service call, and records the resulting enqueue operation through the virtual clock. To adapt a service that normally uses a database or queue, inject the replay implementation at that boundary so the test can start from known state and observe real application outputs deterministically.
