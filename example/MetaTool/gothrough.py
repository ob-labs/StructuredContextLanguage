import json
import os
import csv
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Iterator

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "StructuredContextLanguage"))

from scl.storage.fsstore import fsstore
from scl.meta.functioncall import FunctionCall
from scl.meta.msg import Msg


# ============================================================
# 1. 数据加载接口
# ============================================================

class DatasetLoader:
    """
    加载数据集并提供条目迭代器。
    子类可重写 `iter_entries` 以适配不同格式。
    """

    def __init__(self, data_dir: str, file_names: List[str]):
        self.data_dir = data_dir
        self.file_names = file_names

    def _load_jsonl(self, file_path: str) -> List[Dict[str, Any]]:
        data = []
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    data.append(json.loads(line))
        return data

    def iter_entries(self) -> Iterator[Tuple[str, Dict[str, Any]]]:
        for fname in self.file_names:
            path = os.path.join(self.data_dir, fname)
            if not os.path.exists(path):
                continue
            for entry in self._load_jsonl(path):
                yield fname, entry

    def get_query_text(self, entry: Dict[str, Any]) -> str:
        question = entry.get("question", "")
        return self._extract_query_text(question)

    @staticmethod
    def _extract_query_text(question: Any) -> str:
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
                return DatasetLoader._extract_query_text(first)
        if isinstance(question, dict):
            if "content" in question:
                return question["content"]
            if question.get("role") == "user":
                return question.get("content", "")
        return ""

    def get_provided_functions(self, entry: Dict[str, Any]) -> List[Dict[str, Any]]:
        return entry.get("function", [])

    def get_ground_truth_names(self, entry: Dict[str, Any], source_file: str = None) -> List[str]:
        return entry.get("ground_truth", [])

    def get_entry_id(self, entry: Dict[str, Any]) -> str:
        return entry.get("id", "")


class MetaToolDatasetLoader(DatasetLoader):
    """
    适配 MetaTool 数据集。
    从工具描述 JSON 和查询 CSV 构建条目列表。
    所有条目共享同一套完整的工具定义（候选集）。
    """

    def __init__(self, tool_desc_path: str, query_csv_path: str):
        # 不调用父类 __init__，因为不需要 data_dir / file_names
        self.tool_desc_path = tool_desc_path
        self.query_csv_path = query_csv_path
        self._entries: List[Dict[str, Any]] = []
        self._source_file = os.path.basename(query_csv_path)

        # 1. 加载所有工具定义
        with open(tool_desc_path, 'r', encoding='utf-8') as f:
            self.tool_map: Dict[str, str] = json.load(f)  # {tool_name: description}

        # 2. 构建工具列表（用于 get_provided_functions）
        self._all_tools: List[Dict[str, Any]] = [
            {"name": name, "description": desc}
            for name, desc in self.tool_map.items()
        ]

        # 3. 加载查询 CSV
        with open(query_csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader, None)
            # 如果第一行看起来像标题（第一列包含'query'），则跳过；否则回退重新读取
            if header and header[0].strip().lower() in ("query", "question", "text"):
                pass  # 已跳过标题
            else:
                # 第一行就是数据，需要重新创建 reader 从头开始
                f.seek(0)
                reader = csv.reader(f)
            for row in reader:
                if len(row) < 2:
                    continue
                query = row[0].strip()
                gt_raw = row[1].strip()
                # 若 ground truth 可能包含多个（逗号分隔），则分割
                ground_truth = [gt.strip() for gt in gt_raw.split(",") if gt.strip()]
                self._entries.append({
                    "query": query,
                    "ground_truth": ground_truth
                })

    @property
    def total_entries(self) -> int:
        return len(self._entries)

    def iter_entries(self) -> Iterator[Tuple[str, Dict[str, Any]]]:
        for entry in self._entries:
            yield self._source_file, entry

    def get_query_text(self, entry: Dict[str, Any]) -> str:
        return entry["query"]

    def get_provided_functions(self, entry: Dict[str, Any]) -> List[Dict[str, Any]]:
        # 所有条目都使用同一套完整的工具集
        return self._all_tools

    def get_ground_truth_names(self, entry: Dict[str, Any], source_file: str = None) -> List[str]:
        return entry["ground_truth"]

    def get_entry_id(self, entry: Dict[str, Any]) -> str:
        # 使用查询文本作为 id（或可返回行号）
        return entry["query"]


# ============================================================
# 2. 函数文档构建接口
# ============================================================

class FunctionDocBuilder:
    """
    负责将原始函数定义（dict）转换为描述文本。
    兼容 'parameters' 和 'input_schema' 两种字段。
    """

    def build_doc(self, func_def: Dict[str, Any]) -> str:
        name = func_def.get("name", "")
        desc = func_def.get("description", "")
        params = func_def.get("parameters") or func_def.get("input_schema", {})
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
            except json.JSONDecodeError:
                pass

        return f"{name}. {desc}. " + " ".join(param_texts)


# ============================================================
# 3. 功能存储管理（含相似度去重）
# ============================================================

class CapabilityStore:
    """
    封装 fsstore 的初始化、插入与别名管理。
    """

    def __init__(self, store_path: str, embedding_service: bool = True):
        self.store = fsstore(path=store_path, init=True, embedding_service_on=embedding_service)
        self.alias_map: Dict[str, str] = {}
        self.inserted_names: set = set()
        self.duplicate_pairs: List[Tuple[str, str, str]] = []

    def insert_functions(self,
                         functions: List[Tuple[str, str]],
                         force_on_not_found: bool = True):
        for name, desc in functions:
            cap = FunctionCall(name=name, description=desc)
            try:
                inserted = self.store.insert_capability(cap)
            except Exception as e:
                print(f"插入 {name} 异常: {e}")
                inserted = None

            if inserted is None:
                query_msg = Msg(messages=desc)
                similar = self.store.search_by_similarity(query_msg, limit=1, min_similarity=0.0)
                if similar:
                    rep_name = list(similar.keys())[0]
                    self.alias_map[name] = rep_name
                    self.duplicate_pairs.append((name, rep_name, desc))
                    print(f"⚠️ 功能 '{name}' 因高度相似被拒绝 (返回None)，视为与 '{rep_name}' 重复。")
                else:
                    if force_on_not_found:
                        self.store.insert_capability(cap, force=True)
                        self.alias_map[name] = name
                        self.inserted_names.add(name)
                        print(f"⚠️ 功能 '{name}' 插入失败且未找到相似项，已 force 插入。")
            elif inserted.name != name:
                rep_name = inserted.name
                self.alias_map[name] = rep_name
                self.duplicate_pairs.append((name, rep_name, desc))
                print(f"⚠️ 功能 '{name}' 因高度相似被拒绝，视为与 '{rep_name}' 重复。")
            else:
                self.inserted_names.add(name)

    def export_alias_map(self, path: str = "duplicate_mapping.csv"):
        if self.duplicate_pairs:
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["original_name", "representative_name", "description"])
                writer.writerows(self.duplicate_pairs)
            print(f"重复映射已保存至 {path}")

    def search(self, query: str, limit: int = 5, min_similarity: float = 0.0,
               combine_method: Optional[str] = None, alpha: Optional[float] = None) -> List[str]:
        msg = Msg(messages=query)
        kwargs = {"limit": limit, "min_similarity": min_similarity}
        if combine_method is not None:
            kwargs["combine_method"] = combine_method
        if alpha is not None:
            kwargs["alpha"] = alpha
        results = self.store.search_by_similarity(msg, **kwargs)
        return list(results.keys())

    def get_representative_name(self, original_name: str) -> str:
        return self.alias_map.get(original_name, original_name)


# ============================================================
# 4. 评估器
# ============================================================

class Evaluator:
    def __init__(self, store: CapabilityStore, loader: DatasetLoader):
        self.store = store
        self.loader = loader

    def evaluate(self,
                 combine_method: Optional[str] = None,
                 alpha: Optional[float] = None,
                 limit: int = 5,
                 total_entries: Optional[int] = None,
                 progress_interval: float = 10.0) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        """
        执行评估，可选打印进度。
        """
        detailed = []
        failures = []
        stats = {
            "total_relevant": 0,
            "total_irrelevant": 0,
            "recall_hits": {1: 0, 3: 0, 5: 0, 10: 0},
            "irrelevant_misjudge": 0,
        }

        processed = 0
        next_milestone = progress_interval

        for source_file, entry in self.loader.iter_entries():
            query_text = self.loader.get_query_text(entry)
            if not query_text:
                continue

            true_names = self.loader.get_ground_truth_names(entry, source_file)
            entry_type = "relevant" if true_names else "irrelevant"

            provided_funcs = self.loader.get_provided_functions(entry)
            provided_names_set = {f["name"] for f in provided_funcs}

            mapped_true_names = {self.store.get_representative_name(n) for n in true_names}
            mapped_provided = {self.store.get_representative_name(n) for n in provided_names_set}

            retrieved_names = self.store.search(query_text, limit=limit, combine_method=combine_method, alpha=alpha)
            entry_id = self.loader.get_entry_id(entry)

            for rank, name in enumerate(retrieved_names, 1):
                detailed.append({
                    "source_file": source_file,
                    "entry_id": entry_id,
                    "query": query_text,
                    "rank": rank,
                    "function_name": name,
                    "is_ground_truth": name in mapped_true_names if true_names else False,
                    "is_provided": name in mapped_provided,
                    "ground_truth": ";".join(true_names),
                    "provided_functions": ";".join(sorted(provided_names_set)),
                })

            is_failure = False
            if entry_type == "relevant":
                if not any(name in retrieved_names for name in mapped_true_names):
                    is_failure = True
            else:
                if set(retrieved_names) & mapped_provided:
                    is_failure = True

            if is_failure:
                failures.append({
                    "source_file": source_file,
                    "entry_id": entry_id,
                    "query": query_text,
                    "ground_truth": ";".join(true_names) if true_names else "",
                    "provided_functions": ";".join(sorted(provided_names_set)),
                    "retrieved_top5": ";".join(retrieved_names),
                    "type": entry_type,
                })

            if entry_type == "relevant":
                stats["total_relevant"] += 1
                for k in [1, 3, 5, 10]:
                    if any(name in retrieved_names[:k] for name in mapped_true_names):
                        stats["recall_hits"][k] += 1
            else:
                stats["total_irrelevant"] += 1
                if set(retrieved_names) & mapped_provided:
                    stats["irrelevant_misjudge"] += 1

            processed += 1
            if total_entries and total_entries > 0:
                pct = (processed / total_entries) * 100
                while pct >= next_milestone:
                    print(f"进度: {min(100, int(next_milestone))}% ({processed}/{total_entries})")
                    next_milestone += progress_interval
            else:
                if processed % 100 == 0:
                    print(f"已处理条目: {processed}")

        summary = {
            "total_relevant": stats["total_relevant"],
            "total_irrelevant": stats["total_irrelevant"],
            "recall@1": stats["recall_hits"][1] / stats["total_relevant"] if stats["total_relevant"] else 0.0,
            "recall@3": stats["recall_hits"][3] / stats["total_relevant"] if stats["total_relevant"] else 0.0,
            "recall@5": stats["recall_hits"][5] / stats["total_relevant"] if stats["total_relevant"] else 0.0,
            "recall@10": stats["recall_hits"][10] / stats["total_relevant"] if stats["total_relevant"] else 0.0,
            "misjudge_rate": stats["irrelevant_misjudge"] / stats["total_irrelevant"] if stats["total_irrelevant"] else 0.0,
        }
        return detailed, failures, summary


# ============================================================
# 5. 结果输出
# ============================================================

class ResultWriter:
    @staticmethod
    def save_csv(data: List[Dict[str, Any]], path: str, fieldnames: List[str]):
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)

    @staticmethod
    def print_summary_table(results: List[Tuple[str, str, Dict[str, Any]]]):
        print("\n" + "=" * 110)
        header = f"{'Method':<18}{'Alpha':<6}{'Total Rel':>8}{'Total Irr':>8}{'Top1':>8}{'Top3':>8}{'Top5':>8}{'Top10':>8}{'Misjudge':>10}"
        print(header)
        print("=" * 110)
        for method, alpha_str, summary in results:
            print(f"{method:<18}{alpha_str:<6}"
                  f"{summary['total_relevant']:>8d}{summary['total_irrelevant']:>8d}"
                  f"{summary['recall@1']:>8.3f}{summary['recall@3']:>8.3f}{summary['recall@5']:>8.3f}{summary['recall@10']:>8.3f}"
                  f"{summary['misjudge_rate']:>10.3f}")
        print("=" * 110)


# ============================================================
# 6. 主流程编排
# ============================================================

def build_function_list(loader: DatasetLoader, doc_builder: FunctionDocBuilder) -> List[Tuple[str, str]]:
    name_to_desc = {}
    for _, entry in loader.iter_entries():
        funcs = loader.get_provided_functions(entry)
        if not isinstance(funcs, list):
            continue
        for func in funcs:
            name = func.get("name", "")
            if name and name not in name_to_desc:
                name_to_desc[name] = doc_builder.build_doc(func)
    return list(name_to_desc.items())


def run_benchmark(
    loader: DatasetLoader,
    doc_builder: FunctionDocBuilder,
    store_path: str,
    combine_methods_with_alphas: List[Tuple[Optional[str], Optional[float], str, str]],
    output_prefix: str = "result",
    save_failures: bool = True,
    save_details: bool = True,
):
    print("正在收集所有功能...")
    func_list = build_function_list(loader, doc_builder)
    print(f"去重后功能数: {len(func_list)}")

    cap_store = CapabilityStore(store_path)
    cap_store.insert_functions(func_list)
    print(f"相似合并后独立功能数: {len(cap_store.inserted_names)}")
    cap_store.export_alias_map(f"{output_prefix}_alias_mapping.csv")

    evaluator = Evaluator(cap_store, loader)
    summary_results = []
    detail_fields = ["source_file", "entry_id", "query", "rank", "function_name",
                     "is_ground_truth", "is_provided", "ground_truth", "provided_functions"]
    failure_fields = ["source_file", "entry_id", "query", "ground_truth", "provided_functions",
                      "retrieved_top5", "type"]

    total_entries = getattr(loader, 'total_entries', None)

    for method_val, alpha, method_label, alpha_label in combine_methods_with_alphas:
        print(f"\n评估 combine_method={method_label}, alpha={alpha_label} ...")
        details, failures, summary = evaluator.evaluate(
            combine_method=method_val,
            alpha=alpha,
            total_entries=total_entries,
            progress_interval=10.0
        )
        suffix = f"{method_label}_alpha{alpha_label}".replace(".", "_").replace(" ", "")
        if method_val is None:
            suffix = "none"

        if save_details:
            ResultWriter.save_csv(details, f"{output_prefix}_details_{suffix}.csv", detail_fields)
        if save_failures:
            ResultWriter.save_csv(failures, f"{output_prefix}_failures_{suffix}.csv", failure_fields)

        summary_results.append((method_label, alpha_label, summary))

    ResultWriter.print_summary_table(summary_results)
    return summary_results


# ============================================================
# 示例：在 MetaTool 上使用
# ============================================================

if __name__ == "__main__":
    # MetaTool 数据文件路径（请根据实际情况修改）
    TOOL_DESC_PATH = "/Users/yuanyi/OpenSource/MetaTool/dataset/plugin_des.json"
    QUERY_CSV_PATH = "/Users/yuanyi/OpenSource/MetaTool/dataset/data/all_clean_data.csv"
    STORE_PATH = "./MetaTool_fsstore"

    loader = MetaToolDatasetLoader(TOOL_DESC_PATH, QUERY_CSV_PATH)
    doc_builder = FunctionDocBuilder()

    methods = [
        #(None, None, "none", "-"),
        ("1", 0.1, "minmax", "0.1"),
        #("1", 0.2, "minmax", "0.2"),
    ]

    run_benchmark(
        loader=loader,
        doc_builder=doc_builder,
        store_path=STORE_PATH,
        combine_methods_with_alphas=methods,
        output_prefix="metaTool_benchmark",
    )