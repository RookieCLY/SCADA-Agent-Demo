# Mimo 并发限制实测总结

> 实测日期: 2026-06-11
> API: xiaomi-mimo (mimo-v2.5-pro)
> 限制类型: RPM + TPM 双重限制

## 推荐配置

| 配置类型 | --workers | --rate-limit | 100 trace 预估耗时 |
|----------|-----------|-------------|-------------------|
| A/B/C/D/E (低轮数, 1-3 turns) | **3** | 无需 | ~7 min |
| F_full (多轮, 3-10 turns) | **6** | **3** | ~20 min |

## 推荐命令

```bash
# 简单配置 — 3 并发即可，无需 rate limit
python -m eval.runner --config configs/A_flat_baseline.yaml --all -w 3 --provider xiaomi-mimo

# 复杂配置 — 6 并发 + RPM=3
python -m eval.runner --config configs/F_full_four_in_one.yaml --all -w 6 --rate-limit 3 --provider xiaomi-mimo
```

## 实测数据

### A_flat (低轮数)

| workers | rate_limit | cases | 结果 |
|---------|-----------|-------|------|
| 2 | 0 | 20 | 20/20 ✅ |
| 3 | 0 | 100 | 100/100 ✅ |
| 4 | 0 | 30 | 3/30 ❌ 大量 429 |

**安全上限: w=3, 无需 rate limit。**

### F_full (多轮 workflow+RAG)

| workers | rate_limit | cases | 结果 |
|---------|-----------|-------|------|
| 1 | 0 | 10 | 10/10 ✅ 串行基线 |
| 3 | 0 | 20 | 1/20 ❌ |
| 6 | 3 | 20 | 20/20 ✅ |
| 6 | 5 | 20 | 16/20 ⚠️ 开始出现 429 |
| 6 | 7 | 20 | 14/20 ❌ |

**安全上限: w=6 + rate-limit=3。**

## 原因

mimo 的 RPM 限制作用于**实际 LLM API 调用数**，不是 trace 数。F_full 每个 trace 内有 3-10 次 LLM 调用（workflow 多步 + RAG 检索），w=3 时 burst 瞬间就是 9-30 个并发请求，直接打穿限制。`--rate-limit 3` 将 trace 启动拉平到每分钟 3 个，即使 intra-trace 调用叠加也能承受。
