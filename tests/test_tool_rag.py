"""Tool RAG — hybrid scoring + acceptance hit-rate."""
from __future__ import annotations

import pytest

from agent.tool_rag import (
    HashingTfIdfEncoder,
    ScoredTool,
    ToolIndex,
    build_index_from_registry,
    select_tools,
    simple_rerank,
    tokenise,
)
from agent.tool_registry import build_default_registry


@pytest.fixture(scope="module")
def index() -> ToolIndex:
    return build_index_from_registry(build_default_registry())


# ============================================================ encoder
def test_hashing_encoder_shape():
    enc = HashingTfIdfEncoder(dim=128)
    mat = enc.encode(["hello world", "another", ""])
    assert mat.shape == (3, 128)
    # zero text → zero vector
    assert (mat[2] == 0).all()


def test_hashing_encoder_deterministic():
    enc = HashingTfIdfEncoder(dim=64)
    a = enc.encode(["反应釜 温度 报警"])[0]
    b = enc.encode(["反应釜 温度 报警"])[0]
    assert (a == b).all()


def test_tokenise_cjk():
    assert "反" in tokenise("反应釜")
    assert "temp_101" in tokenise("TEMP_101 over-temperature")


# ============================================================ index basics
def test_index_size_matches_registry(index: ToolIndex):
    assert len(index.atomics) >= 16  # we have at least 16 atomic tools now


def test_rank_returns_topn(index: ToolIndex):
    out = index.rank("create alarm", top_n=5)
    assert 1 <= len(out) <= 5
    assert all(isinstance(s, ScoredTool) for s in out)


def test_rank_candidates_filter(index: ToolIndex):
    out = index.rank(
        "anything",
        candidates={"create_analog_alarm", "create_digital_alarm"},
        top_n=10,
    )
    names = {s.name for s in out}
    assert names <= {"create_analog_alarm", "create_digital_alarm"}


def test_rank_alpha_extremes(index: ToolIndex):
    pure_dense = index.rank("temperature alarm", alpha=1.0, top_n=5)
    pure_sparse = index.rank("temperature alarm", alpha=0.0, top_n=5)
    # both should return *something* sensible
    assert pure_dense and pure_sparse


def test_simple_rerank_name_boost(index: ToolIndex):
    # Query contains the exact atomic name verbatim — name_boost should pin it
    # to the top regardless of any incidental BM25 noise.
    query = "please invoke create_analog_alarm now"
    ranked = index.rank(query, top_n=10, alpha=0.5)
    reranked = simple_rerank(query, ranked)
    assert reranked[0].name == "create_analog_alarm"


def test_rank_domains_aggregates(index: ToolIndex):
    out = index.rank_domains("create alarm", top_n=10, alpha=0.6)
    domains = {s.name for s in out}
    assert "manage_alarms" in domains


# ============================================================ golden-Top-K hit-rate
# Each tuple is (natural-language query, atomic tool that *should* appear in Top-K)
GOLDEN_QUERIES: list[tuple[str, str]] = [
    # alarms
    ("给反应釜温度加个高温报警", "create_analog_alarm"),
    ("锅炉超温告警", "create_analog_alarm"),
    ("pump fault digital alarm", "create_digital_alarm"),
    ("调整报警上限阈值", "set_threshold"),
    ("关掉这个报警", "disable_alarm"),
    ("把这个报警删掉", "delete_alarm"),
    # points
    ("新建一个温度点位", "create_point"),
    ("把温度点的量程改成 0~200", "update_point"),
    ("列出所有点位", "list_points"),
    # pages / binding
    ("新建一个监控画面", "create_page"),
    ("画面上加一个温度计图元", "create_widget"),
    ("把温度点绑定到温度计的 value", "bind_point"),
    ("列出所有画面", "list_pages"),
    # graphics
    ("画一个矩形", "create_rect"),
    ("在画面上画一根管道", "create_line"),
    ("加一段文字标签", "create_text"),
    ("把这几个图元横向排列", "apply_flow_layout"),
    ("把这两个图元组合成一组", "group_widgets"),
    ("把矩形改成红色", "set_widget_style"),
    ("删掉这个图元", "delete_widget"),
    # history
    ("开启 TEMP_101 的历史归档", "enable_history"),
    ("暂停历史记录", "disable_history"),
    ("把保留时间改成 90 天", "set_retention"),
    ("查最近一分钟的历史趋势", "query_history"),
    # scripts
    ("添加一个周期脚本", "create_script"),
    ("修改脚本内容", "update_script_body"),
    ("列出所有 on_change 脚本", "list_scripts"),
    # deployment
    ("校验项目能不能下装", "validate_project"),
    ("把项目下发", "deploy_project"),
    ("回滚刚才的下装", "rollback_deployment"),
]


def test_topk_hit_rate(index: ToolIndex):
    """Target: ≥ 80% of golden queries surface the expected atomic in Top-10."""
    hits = 0
    misses: list[tuple[str, str, list[str]]] = []
    for query, expected in GOLDEN_QUERIES:
        ranked = select_tools(
            query,
            index=index,
            allowed_atomics=None,
            top_n=30,
            top_k=10,
            alpha=0.6,
            use_reranker=True,
        )
        names = [s.name for s in ranked]
        if expected in names:
            hits += 1
        else:
            misses.append((query, expected, names[:5]))
    rate = hits / len(GOLDEN_QUERIES)
    assert rate >= 0.80, (
        f"Top-K hit-rate too low: {rate:.2%} ({hits}/{len(GOLDEN_QUERIES)}); "
        f"misses (≤5): {misses[:5]}"
    )


def test_select_tools_with_state_filter(index: ToolIndex):
    """Hard filter ∩ soft rank — when the state forbids create_analog_alarm,
    it must NOT appear in the top-K even if the query begs for it."""
    allowed = {"list_points", "list_pages"}
    out = select_tools(
        "给反应釜温度加个高温报警",
        index=index,
        allowed_atomics=allowed,
        top_n=30,
        top_k=10,
    )
    names = {s.name for s in out}
    assert names <= allowed
    assert "create_analog_alarm" not in names
