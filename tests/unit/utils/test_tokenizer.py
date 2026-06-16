"""tokenizer 工具单元测试——验证降级与失败缓存行为。"""

import app.utils.tokenizer as tok


def _reset_state() -> None:
    """重置模块级加载状态，隔离测试间副作用。"""
    tok._tokenizer = None
    tok._load_attempted = False


# ── _char_estimate 纯函数 ────────────────────────────────────────────────────


def test_char_estimate_returns_positive_for_cjk() -> None:
    assert tok._char_estimate("你好世界") > 0


def test_char_estimate_returns_positive_for_ascii() -> None:
    assert tok._char_estimate("hello world") > 0


def test_char_estimate_never_zero_for_nonempty() -> None:
    assert tok._char_estimate("x") >= 1


# ── 加载失败缓存（回归测试）─────────────────────────────────────────────────


def test_failed_load_attempted_only_once(monkeypatch) -> None:
    """tokenizer 加载失败后必须缓存失败状态，不能每次 count_tokens 都重试。

    回归：原实现用 @lru_cache 不缓存抛异常的调用，导致加载失败时
    每次调用都重新联网重试，拖死服务。
    """
    _reset_state()
    calls: list[int] = []

    def boom(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("offline")

    monkeypatch.setattr("transformers.AutoTokenizer.from_pretrained", boom)

    first = tok.count_tokens("hello world")
    second = tok.count_tokens("你好世界")

    assert first > 0 and second > 0  # 降级返回正整数，服务不中断
    assert len(calls) == 1  # 失败只尝试加载一次
    _reset_state()


def test_count_tokens_falls_back_when_load_fails(monkeypatch) -> None:
    """加载失败时 count_tokens 应返回字符估算值。"""
    _reset_state()

    def boom(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr("transformers.AutoTokenizer.from_pretrained", boom)

    text = "一些中文和 some english"
    assert tok.count_tokens(text) == tok._char_estimate(text)
    _reset_state()
