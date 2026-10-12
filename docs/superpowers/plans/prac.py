"""分组查询注意力（GQA），仅使用 Python 标准库。

输入格式（数据可以用任意空白分隔）：
    n h_q h_kv d_k d_v
    Q: n 行，每行 h_q * d_k 个数
    K: n 行，每行 h_kv * d_k 个数
    V: n 行，每行 h_kv * d_v 个数
    mask: n 行，每行 n 个数（0 保留，1 将对应分数设为 -inf）
输出 n 行，每行 h_q * d_v 个数，保留两位小数。
本轮试验：整行屏蔽时按均匀权重处理，输出对应 V 头的逐维均值。
"""

import math
import sys


def grouped_query_attention(q, k, v, mask, h_q, h_kv, d_k, d_v):
    """计算缩放点积注意力；mask[i][j] == 1 时屏蔽对应位置。"""
    if min(h_q, h_kv, d_k, d_v) <= 0 or h_q % h_kv:
        raise ValueError("头数和维度必须为正，且 Q 头数必须是 KV 头数的整数倍")

    n = len(q)
    group_size = h_q // h_kv
    scale = 1.0 / math.sqrt(d_k)

    # 1. 拆分输入矩阵：每个 token 的连续列对应一个注意力头。
    q_heads = [[row[h * d_k:(h + 1) * d_k] for row in q]
               for h in range(h_q)]
    k_heads = [[row[h * d_k:(h + 1) * d_k] for row in k]
               for h in range(h_kv)]
    v_heads = [[row[h * d_v:(h + 1) * d_v] for row in v]
               for h in range(h_kv)]

    output = [[] for _ in range(n)]
    # 2. 分组计算：连续 group_size 个 Q 头共享同一个 KV 头。
    for head in range(h_q):
        kv_head = head // group_size
        for i in range(n):
            scores = [
                -math.inf if mask[i][j] == 1 else
                sum(a * b for a, b in zip(q_heads[head][i], key)) * scale
                for j, key in enumerate(k_heads[kv_head])
            ]
            maximum = max(scores)
            if maximum == -math.inf:
                # 试验有限极小值掩码的行为：分数全相同时权重均为 1/n。
                head_output = [
                    sum(row[d] for row in v_heads[kv_head]) / n
                    for d in range(d_v)
                ]
            else:
                # 减去最大分数，避免指数运算溢出。
                weights = [math.exp(score - maximum) for score in scores]
                denominator = sum(weights)
                weights = [weight / denominator for weight in weights]
                head_output = [
                    sum(weights[j] * v_heads[kv_head][j][d]
                        for j in range(n))
                    for d in range(d_v)
                ]

            # 3. 按 Q 头的原始顺序拼接输出。
            output[i].extend(head_output)
    return output


def main():
    tokens = sys.stdin.buffer.read().split()
    if not tokens:
        return
    n, h_q, h_kv, d_k, d_v = map(int, tokens[:5])
    if min(n, h_q, h_kv, d_k, d_v) <= 0 or h_q % h_kv:
        raise ValueError("长度、头数和维度必须为正，且 Q 头数必须能被 KV 头数整除")
    expected = 5 + n * (h_q * d_k + h_kv * d_k + h_kv * d_v + n)
    if len(tokens) != expected:
        raise ValueError(f"输入应包含 {expected} 个数，实际为 {len(tokens)} 个")
    values = iter(map(float, tokens[5:]))

    def read_matrix(columns):
        return [[next(values) for _ in range(columns)] for _ in range(n)]

    q = read_matrix(h_q * d_k)
    k = read_matrix(h_kv * d_k)
    v = read_matrix(h_kv * d_v)
    mask = read_matrix(n)
    result = grouped_query_attention(q, k, v, mask, h_q, h_kv, d_k, d_v)
    sys.stdout.write("\n".join(" ".join(f"{x:.2f}" for x in row)
                               for row in result) + "\n")


if __name__ == "__main__":
    main()
