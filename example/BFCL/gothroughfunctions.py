import json
import os
import sys
import csv
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "StructuredContextLanguage"))

from scl.storage.fsstore import fsstore
from scl.meta.functioncall import FunctionCall
from scl.meta.msg import Msg
from typing import Dict, Any, List, Optional, Tuple

# ------------------------------
# 配置
# ------------------------------
DATA_DIR = "/Users/yuanyi/OpenSource/gorilla/berkeley-function-call-leaderboard/bfcl_eval/data"
ANSWER_DIR = os.path.join(DATA_DIR, "possible_answer")
TARGET_FILES = [
    "BFCL_v4_irrelevance.json",
    "BFCL_v4_live_irrelevance.json",
    "BFCL_v4_live_multiple.json",
    "BFCL_v4_live_parallel_multiple.json",
    "BFCL_v4_live_parallel.json",
    "BFCL_v4_live_relevance.json",
]
STORE_PATH = "./bfcl_fsstore"

# 需要遍历 alpha 的 combine_method 及其标签
METHODS_WITH_ALPHA: List[Tuple[str, str]] = [
    ("1", "minmax"),
    ("2", "sigmoid"),
    ("3", "tanh"),
    ("4", "minmax_sigmoid"),
    ("5", "minmax_tanh"),
]
ALPHAS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]

# ------------------------------
# 工具函数（与原有完全相同）
# ------------------------------
def load_jsonl(file_path: str) -> List[Dict[str, Any]]:
    data = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(json.loads(line))
    return data

def load_possible_answers(answer_dir: str, file_name: str) -> Dict[str, List[str]]:
    base = os.path.splitext(file_name)[0]
    possible_names = [
        os.path.join(answer_dir, f"{base}_answer.json"),
        os.path.join(answer_dir, f"{base}.json"),
        os.path.join(answer_dir, file_name),
    ]
    answer_path = None
    for p in possible_names:
        if os.path.exists(p):
            answer_path = p
            break
    if answer_path is None:
        return {}
    answers = {}
    with open(answer_path, 'r', encoding='utf-8') as f:
        for line in f:
            obj = json.loads(line.strip())
            entry_id = obj["id"]
            gt = obj.get("ground_truth", [])
            names = []
            if isinstance(gt, list):
                for item in gt:
                    if isinstance(item, str):
                        names.append(item)
                    elif isinstance(item, dict):
                        names.extend(item.keys())
            answers[entry_id] = names
    return answers

def build_function_doc(func: Dict[str, Any]) -> str:
    name = func.get("name", "")
    desc = func.get("description", "")
    params = func.get("parameters", {})
    properties = params.get("properties", {})
    param_texts = []
    if isinstance(properties, dict):
        for pname, pinfo in properties.items():
            pdesc = pinfo.get("description", "")
            ptype = pinfo.get("type", "")
            param_texts.append(f"{pname} ({ptype}): {pdesc}")
    elif isinstance(properties, str):
        try:
            props_dict = json.loads(properties)
            for pname, pinfo in props_dict.items():
                pdesc = pinfo.get("description", "")
                ptype = pinfo.get("type", "")
                param_texts.append(f"{pname} ({ptype}): {pdesc}")
        except:
            pass
    return f"{name}. {desc}. " + " ".join(param_texts)

def get_user_query(question: Any) -> str:
    if isinstance(question, str):
        return question
    if isinstance(question, list) and len(question) > 0:
        first = question[0]
        if isinstance(first, dict):
            for msg in question:
                if isinstance(msg, dict) and msg.get("role") == "user":
                    return msg.get("content", "")
            if "content" in first:
                return first["content"]
            return ""
        elif isinstance(first, list):
            return get_user_query(first)
        else:
            return ""
    if isinstance(question, dict):
        if "content" in question:
            return question["content"]
        if question.get("role") == "user":
            return question.get("content", "")
    return ""

# ------------------------------
# 评估函数（增加 alpha 参数）
# ------------------------------
def evaluate_method(store, files: List[str], answer_dir: str,
                    combine_method: Optional[str], alpha: Optional[float] = None) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    per_file_stats = defaultdict(lambda: {
        "total_relevant": 0, "total_irrelevant": 0,
        "recall_hits": {k: 0 for k in [1, 3, 5]},
        "irrelevant_misjudge": 0,
    })
    all_results = []

    for fname in files:
        file_path = os.path.join(DATA_DIR, fname)
        if not os.path.exists(file_path):
            continue
        entries = load_jsonl(file_path)
        answers = load_possible_answers(answer_dir, fname)
        stats = per_file_stats[fname]

        for entry in entries:
            query_text = get_user_query(entry.get("question", []))
            if not query_text:
                continue
            true_names = answers.get(entry["id"], [])
            provided_funcs = set()
            if isinstance(entry.get("function"), list):
                provided_funcs = {f["name"] for f in entry["function"]}

            gt_str = ";".join(true_names)
            provided_str = ";".join(sorted(provided_funcs))

            msg = Msg(messages=query_text)

            # 传递 alpha 参数（仅当 combine_method 不为 None）
            kwargs = {"limit": 5, "min_similarity": 0.0, "combine_method": combine_method}
            if combine_method is not None and alpha is not None:
                kwargs["alpha"] = alpha
            results = store.search_by_similarity(msg, **kwargs)
            retrieved_names = list(results.keys())

            for rank, name in enumerate(retrieved_names, 1):
                all_results.append({
                    "source_file": fname,
                    "entry_id": entry["id"],
                    "query": query_text,
                    "rank": rank,
                    "function_name": name,
                    "is_ground_truth": name in true_names if true_names else False,
                    "is_provided": name in provided_funcs,
                    "ground_truth": gt_str,
                    "provided_functions": provided_str,
                })

            if true_names:
                stats["total_relevant"] += 1
                for k in [1, 3, 5]:
                    if any(name in retrieved_names[:k] for name in true_names):
                        stats["recall_hits"][k] += 1
            else:
                stats["total_irrelevant"] += 1
                if set(retrieved_names) & provided_funcs:
                    stats["irrelevant_misjudge"] += 1

    return per_file_stats, all_results

# ------------------------------
# 主流程
# ------------------------------
def main():
    # 1. 收集函数并插入 fsstore（仅一次）
    unique_funcs: Dict[str, str] = {}
    print("正在加载 BFCL 数据...")
    for fname in TARGET_FILES:
        path = os.path.join(DATA_DIR, fname)
        if not os.path.exists(path):
            continue
        entries = load_jsonl(path)
        for entry in entries:
            functions = entry.get("function", [])
            if not isinstance(functions, list):
                continue
            for func in functions:
                name = func.get("name", "")
                if name and name not in unique_funcs:
                    unique_funcs[name] = build_function_doc(func)

    print(f"去重后唯一函数数: {len(unique_funcs)}，正在初始化 fsstore...")
    store = fsstore(path=STORE_PATH, init=True, embedding_service_on=True)
    for name, desc in unique_funcs.items():
        cap = FunctionCall(name=name, description=desc)
        try:
            store.insert_capability(cap)
        except Exception:
            pass

    print("开始评估所有 combine_method 和 alpha 组合...\n")

    overall_summary = []  # 存储 (method_label, alpha_value, total_rel, total_irr, top1, top3, top5, misjudge)

    # --- 先评估 combine_method = None（纯粹 BM25 + embedding 混合？实际上是自动选择）---
    print(">>> 正在评估 combine_method = None")
    per_file_stats, all_results = evaluate_method(store, TARGET_FILES, ANSWER_DIR, None)
    csv_path = "fsstore_results_none.csv"
    fieldnames = ["source_file", "entry_id", "query", "rank", "function_name",
                  "is_ground_truth", "is_provided", "ground_truth", "provided_functions"]
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)
    print(f"    结果已保存至 {csv_path}")

    total_rel = sum(s["total_relevant"] for s in per_file_stats.values())
    total_irr = sum(s["total_irrelevant"] for s in per_file_stats.values())
    hits = {k: sum(s["recall_hits"][k] for s in per_file_stats.values()) for k in [1, 3, 5]}
    total_mis = sum(s["irrelevant_misjudge"] for s in per_file_stats.values())
    overall_summary.append(("none", "-",
                            total_rel, total_irr,
                            hits[1]/total_rel if total_rel else 0.0,
                            hits[3]/total_rel if total_rel else 0.0,
                            hits[5]/total_rel if total_rel else 0.0,
                            total_mis/total_irr if total_irr else 0.0))

    # --- 遍历所有带 alpha 的 method ---
    for method_val, method_label in METHODS_WITH_ALPHA:
        for alpha in ALPHAS:
            label = f"{method_label} (α={alpha:.1f})"
            print(f">>> 正在评估 combine_method = {method_val}, alpha = {alpha:.1f}")
            per_file_stats, all_results = evaluate_method(store, TARGET_FILES, ANSWER_DIR,
                                                         method_val, alpha=alpha)
            csv_path = f"fsstore_results_{method_label}_alpha{alpha:.1f}.csv"
            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(all_results)
            print(f"    结果已保存至 {csv_path}")

            total_rel = sum(s["total_relevant"] for s in per_file_stats.values())
            total_irr = sum(s["total_irrelevant"] for s in per_file_stats.values())
            hits = {k: sum(s["recall_hits"][k] for s in per_file_stats.values()) for k in [1, 3, 5]}
            total_mis = sum(s["irrelevant_misjudge"] for s in per_file_stats.values())
            overall_summary.append((method_label, f"{alpha:.1f}",
                                    total_rel, total_irr,
                                    hits[1]/total_rel if total_rel else 0.0,
                                    hits[3]/total_rel if total_rel else 0.0,
                                    hits[5]/total_rel if total_rel else 0.0,
                                    total_mis/total_irr if total_irr else 0.0))

    # 打印整体对比表格
    print("\n" + "=" * 110)
    print(f"{'Method':<18}{'Alpha':<6}{'Total Rel':>8}{'Total Irr':>8}{'Top1':>8}{'Top3':>8}{'Top5':>8}{'Misjudge':>10}")
    print("=" * 110)
    for (method, alpha_str, total_rel, total_irr, top1, top3, top5, misjudge) in overall_summary:
        print(f"{method:<18}{alpha_str:<6}{total_rel:>8d}{total_irr:>8d}"
              f"{top1:>8.3f}{top3:>8.3f}{top5:>8.3f}{misjudge:>10.3f}")
    print("=" * 110)

if __name__ == "__main__":
    main()