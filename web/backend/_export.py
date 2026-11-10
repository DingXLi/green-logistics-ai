"""Green Logistics AI —— 导出编解码 helper（iter #83 从 main.py 外置）。

这些 helper 有两个用户：main.py 的 ``/api/admin/db-export`` 和
``routers/persistence.py`` 的 20 个 ``/api/persistence/export/*`` 端点。
留在 main.py 里的话，router 又得反向 import main → 循环依赖。

外置之后依赖方向单向：

    main.py ──▶ _export.py
    routers/persistence.py ──▶ _export.py

对外行为不变：``from web.backend.main import _rows_to_csv`` 仍然可用。
"""

import csv
import gzip
import io
from typing import Any, Dict, List

from fastapi.responses import Response as FastAPIResponse


# ============================================
# iter #18: DB export helpers
# ============================================
def _csv_to_rows(csv_str: str) -> List[Dict[str, Any]]:
    """Convert CSV string → list of dicts.

    Skips metadata header lines (starting with '#') that may be present
    when CSV was exported with include_metadata=True (iter #20+).
    """
    # Filter out # comment lines (metadata header)
    lines = [line for line in csv_str.split("\n") if not line.startswith("#")]
    csv_clean = "\n".join(lines)
    reader = csv.DictReader(io.StringIO(csv_clean))
    return [dict(r) for r in reader]


def _rows_to_csv(rows: List[Dict[str, Any]]) -> str:
    """Convert list of dicts → CSV string."""
    if not rows:
        return ""
    buf = io.StringIO()
    fieldnames = list(rows[0].keys())
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()


# ============================================
# iter #23: Parquet helpers (columnar analytics)
# ============================================
PARQUET_MIMETYPE = "application/vnd.apache.parquet"


def _rows_to_parquet_bytes(rows: List[Dict[str, Any]]) -> bytes:
    """
    把 list of dicts → Apache Parquet binary (iter #23)。

    优势 vs CSV:
    - Columnar storage (analytics query 更快)
    - 内嵌 schema (typed columns, no parse needed)
    - 压缩更好 (snappy 默认, 比 CSV gzip 还小 ~3-5x)
    - pandas/polars/duckdb/spark 原生支持

    处理:
    - 强制 schema 推断 (pyarrow default)
    - 空 rows → 返回最小 parquet (1 行 schema-only, schema 在 metadata 里)
    - 混合类型列 → 转 string (避免 pyarrow type conflict)
    """
    if not rows:
        # 返回空 parquet: schema-only table with no rows
        # pyarrow schema with single nullable int64 column (任何 schema 都可以)
        import pyarrow as pa
        schema = pa.schema([("_empty", pa.int64())])
        empty_table = pa.table({"_empty": pa.array([], type=pa.int64())}, schema=schema)
        sink = io.BytesIO()
        import pyarrow.parquet as pq
        pq.write_table(empty_table, sink, compression="snappy")
        return sink.getvalue()

    # Build DataFrame + handle type coercion (iter #23 best-effort)
    import pandas as pd
    df = pd.DataFrame(rows)

    # Convert mixed-type object columns to string (avoid pyarrow type errors)
    for col in df.columns:
        if df[col].dtype == "object":
            # Try to keep numeric if all values are numeric
            try:
                # Quick test: can it be int?
                pd.to_numeric(df[col], errors="raise")
                # keep numeric
            except (ValueError, TypeError):
                # stringify
                df[col] = df[col].astype(str).replace({"None": "", "nan": ""})

    table = __import__("pyarrow").Table.from_pandas(df, preserve_index=False)
    sink = io.BytesIO()
    import pyarrow.parquet as pq
    pq.write_table(table, sink, compression="snappy")
    return sink.getvalue()


def _maybe_gzip(content: bytes, use_gzip: bool, filename: str) -> FastAPIResponse:
    """可选 gzip 包装 (iter #19)。返回 Response with Content-Encoding header。

    Args:
        content: raw bytes (text content as UTF-8)
        use_gzip: 是否启用 gzip
        filename: 下载文件名
    """
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    if use_gzip:
        compressed = gzip.compress(content)
        headers["Content-Encoding"] = "gzip"
        return FastAPIResponse(
            content=compressed,
            media_type="application/octet-stream",
            headers=headers,
        )
    # not gzipped — content 直接传
    return FastAPIResponse(
        content=content,
        headers=headers,
    )
