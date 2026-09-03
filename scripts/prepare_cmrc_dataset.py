"""
将 CMRC2018 格式的数据集转换为可上传的 txt 文件。

用法：
    python scripts/prepare_cmrc_dataset.py --input dev.json --output ./test_docs

输出：
    test_docs/
        cmrc_0.txt    ← 每个 context 一个文件
        cmrc_1.txt
        ...
        qa_pairs.json ← 所有问答对，供后续验证检索效果
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def convert(input_path: str, output_dir: str, max_docs: int = 50) -> None:
    src = Path(input_path)
    dst = Path(output_dir)
    dst.mkdir(parents=True, exist_ok=True)

    with src.open(encoding="utf-8") as f:
        dataset = json.load(f)

    qa_pairs: list[dict] = []
    doc_count = 0

    for article in dataset.get("data", []):
        for para in article.get("paragraphs", []):
            if doc_count >= max_docs:
                break

            context: str = para.get("context", "").strip()
            if not context:
                continue

            # 每个 context 存为独立 txt 文件
            txt_file = dst / f"cmrc_{doc_count}.txt"
            txt_file.write_text(context, encoding="utf-8")

            # 收集问答对
            for qa in para.get("qas", []):
                answers = [a["text"] for a in qa.get("answers", [])]
                if answers:
                    qa_pairs.append(
                        {
                            "doc_file": txt_file.name,
                            "question": qa["question"],
                            "answer": answers[0],  # 取第一个答案
                            "context_snippet": context[:100] + "...",
                        }
                    )

            doc_count += 1

    # 保存问答对供后续验证
    qa_file = dst / "qa_pairs.json"
    qa_file.write_text(
        json.dumps(qa_pairs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"生成 {doc_count} 个 txt 文件 → {dst}/")
    print(f"共 {len(qa_pairs)} 个问答对 → {qa_file}")
    print("\n示例问题：")
    for qa in qa_pairs[:3]:
        print(f"  Q: {qa['question']}")
        print(f"  A: {qa['answer']}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="CMRC JSON 文件路径")
    parser.add_argument("--output", default="./test_docs", help="输出目录")
    parser.add_argument("--max", type=int, default=50, help="最多提取多少篇文章（默认 50）")
    args = parser.parse_args()

    convert(args.input, args.output, args.max)
