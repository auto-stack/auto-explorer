#!/usr/bin/env python3
"""mkbig — 生成可控规模测试目录（PLAN-001 T-06/T16/T18 用）。

布局：i%10==0 为目录 dir-NNNNN，其余文件 file-NNNNN.txt（内容长度 i%97，
名字自然序 = 数字序，目录交错分布）。幂等：已足额即跳过。

用法：python mkbig.py <N> <dir>   （目录不存在则创建）
"""
import os
import sys


def mkbig(n, root):
    d = os.path.join(root, f"big{n}")
    os.makedirs(d, exist_ok=True)
    existing = len(os.listdir(d))
    if existing < n:
        for i in range(existing, n):
            if i % 10 == 0:
                os.makedirs(os.path.join(d, f"dir-{i:05d}"), exist_ok=True)
            else:
                p = os.path.join(d, f"file-{i:05d}.txt")
                with open(p, "w", encoding="utf-8") as f:
                    f.write("x" * (i % 97))
    return d


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    print(mkbig(int(sys.argv[1]), sys.argv[2]))
