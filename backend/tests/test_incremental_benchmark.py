from benchmark.scripts.run_incremental import direct_llama_metrics, synthetic_turn


def test_direct_metrics_only_report_observed_cache_reuse() -> None:
    body = "\n".join(
        [
            'data: {"choices":[{"delta":{"content":"Oui"}}]}',
            (
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
                '"usage":{"prompt_tokens":100,"completion_tokens":1},'
                '"timings":{"prompt_n":25,"prompt_ms":50,"predicted_ms":10,'
                '"predicted_per_second":100}}'
            ),
            "data: [DONE]",
        ]
    )

    answer, metrics, terminal = direct_llama_metrics(body)

    assert answer == "Oui"
    assert terminal == "done"
    assert metrics["evaluated_prompt_tokens"] == 25
    assert metrics["reused_prompt_tokens"] == 75
    assert metrics["cache_reuse_observable"] is True


def test_incremental_turn_factory_is_deterministic() -> None:
    assert synthetic_turn(500, 42, 1) == synthetic_turn(500, 42, 1)
